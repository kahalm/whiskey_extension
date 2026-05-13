import asyncio
import logging
import random
import re
import string

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from playwright_stealth import Stealth

from scraper.db import (
    get_connection, init_db, save_whisky, save_whisky_basic,
    get_last_wbid, set_last_wbid, get_whisky_count,
    get_search_state, set_search_state, has_wbid,
)

log = logging.getLogger(__name__)

BASE_URL = "https://www.whiskybase.com/whiskies/whisky"
SEARCH_URL = "https://www.whiskybase.com/search-v1/whisky"


# ---------------------------------------------------------------------------
# Phase 1: Bulk-collect WBIDs + basic data via search
# ---------------------------------------------------------------------------

# Systematic query list to cover as many whiskies as possible
SEARCH_QUERIES = (
    # Single letters
    list(string.ascii_lowercase)
    # Two-letter combos for common starts
    + [a + b for a in string.ascii_lowercase for b in string.ascii_lowercase]
    # Digits (years, ages)
    + [str(y) for y in range(1900, 2027)]
    + [str(n) for n in range(1, 100)]
)


def parse_search_results(html: str) -> list[dict]:
    """Parse search results table, returning basic whisky data with WBIDs."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    table = soup.select_one("table.whiskytable")
    if not table:
        return results

    # Columns (11 cells): [0]#  [1]img  [2]Name(link)  [3]Age  [4]Strength
    #   [5]Size  [6]Bottled  [7]Cask#  [8]Barcode  [9]Rating  [10]empty
    for row in table.find_all("tr")[1:]:  # skip header
        cells = row.find_all("td")
        if len(cells) < 10:
            continue

        # Extract WBID from link in Name cell [2]
        name_cell = cells[2]
        link = name_cell.find("a", href=True)
        if not link or "/whiskies/whisky/" not in link["href"]:
            continue

        match = re.search(r"/whiskies/whisky/(\d+)", link["href"])
        if not match:
            continue

        wbid = int(match.group(1))
        name = link.get_text(strip=True)

        # Extract image URL from cell [1]
        image_url = None
        img_link = cells[1].find("a", href=True)
        if img_link:
            src = img_link.get("href", "")
            if src and "default" not in src:
                image_url = src

        age = cells[3].get_text(strip=True) or None
        strength = cells[4].get_text(strip=True) or None
        size = cells[5].get_text(strip=True) or None
        bottled = cells[6].get_text(strip=True) or None
        cask_number = cells[7].get_text(strip=True) or None
        barcode = cells[8].get_text(strip=True) or None
        rating_text = cells[9].get_text(strip=True) or None

        rating = None
        if rating_text:
            try:
                rating = float(rating_text)
            except ValueError:
                pass

        results.append({
            "wbid": wbid,
            "name": name or None,
            "age": age,
            "strength": strength,
            "size": size,
            "bottled": bottled,
            "cask_number": cask_number,
            "barcode": barcode,
            "rating": rating,
            "image_url": image_url,
            "url": f"{BASE_URL}/{wbid}",
        })

    return results


async def search_whiskies(page: Page, query: str) -> list[dict]:
    """Fetch search results for a query and return parsed whisky data."""
    url = f"{SEARCH_URL}?q={query}"
    try:
        response = await page.goto(url, wait_until="load", timeout=60000)
        if not response or response.status != 200:
            log.warning("Search '%s': HTTP %s", query, response.status if response else "None")
            return []

        await page.wait_for_selector("table.whiskytable, .no-results, h1", timeout=15000)
        await asyncio.sleep(1)

        html = await page.content()
        return parse_search_results(html)

    except Exception as e:
        log.error("Search '%s': %s", query, e)
        return []


async def run_search_collector(
    delay_min: float = 2.0,
    delay_max: float = 5.0,
    headless: bool = True,
):
    """Phase 1: Collect WBIDs and basic data via search queries."""
    conn = get_connection()
    init_db(conn)

    last_query = get_search_state(conn)
    start_idx = 0
    if last_query:
        try:
            start_idx = SEARCH_QUERIES.index(last_query) + 1
        except ValueError:
            start_idx = 0

    remaining = SEARCH_QUERIES[start_idx:]
    log.info(
        "Search collector: %d queries remaining (starting from '%s'), %d whiskies in DB",
        len(remaining),
        remaining[0] if remaining else "done",
        get_whisky_count(conn),
    )

    if not remaining:
        log.info("All search queries already processed.")
        conn.close()
        return

    total_new = 0

    async with async_playwright() as p:
        browser, context, page = await _create_browser(p, headless)
        await _warmup(page)

        try:
            for i, query in enumerate(remaining):
                results = await search_whiskies(page, query)

                new_count = 0
                for item in results:
                    if not has_wbid(conn, item["wbid"]):
                        save_whisky_basic(conn, item)
                        new_count += 1

                total_new += new_count
                set_search_state(conn, query)

                log.info(
                    "[%d/%d] q='%s': %d results, %d new (total in DB: %d)",
                    start_idx + i + 1,
                    len(SEARCH_QUERIES),
                    query,
                    len(results),
                    new_count,
                    get_whisky_count(conn),
                )

                delay = random.uniform(delay_min, delay_max)
                await asyncio.sleep(delay)

        except KeyboardInterrupt:
            log.info("Interrupted at query '%s'", query)
        finally:
            await browser.close()
            conn.close()

    log.info("Search collector done. %d new whiskies added.", total_new)


# ---------------------------------------------------------------------------
# Phase 2: Detail crawler for individual whisky pages
# ---------------------------------------------------------------------------

def parse_whisky_page(html: str, wbid: int) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")

    name_el = soup.select_one("h1")
    if not name_el:
        return None

    name = name_el.get_text(strip=True)
    if not name or "not found" in name.lower() or "error" in name.lower():
        return None

    # Extract key-value pairs from <dl>/<dt>/<dd>
    attrs = {}
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            key = dt.get_text(strip=True)
            val = dd.get_text(strip=True)
            if key and val:
                attrs[key] = val

    if "Category" not in attrs and "Distillery" not in attrs:
        return None

    rating = None
    votes = None
    rating_el = soup.select_one("span.votes-rating-current")
    if rating_el:
        try:
            rating = float(rating_el.get_text(strip=True))
        except ValueError:
            pass
    votes_el = soup.select_one("dd.votes-count")
    if votes_el:
        try:
            votes = int(votes_el.get_text(strip=True).replace(",", "").replace(".", ""))
        except ValueError:
            pass

    image_url = None
    img_el = soup.select_one("a.whisky-image-big img, .photo-holder img, .block-photo img")
    if img_el:
        src = img_el.get("src", "")
        if "default" not in src:
            image_url = src

    return {
        "wbid": wbid,
        "name": name,
        "distillery": attrs.get("Distillery"),
        "district": attrs.get("District"),
        "country": attrs.get("Country"),
        "age": attrs.get("Stated Age") or attrs.get("Age"),
        "strength": attrs.get("Strength"),
        "size": attrs.get("Size"),
        "bottler": attrs.get("Bottler"),
        "bottling_serie": attrs.get("Bottling series") or attrs.get("Bottling serie"),
        "cask_type": attrs.get("Cask Type") or attrs.get("Casktype"),
        "cask_number": attrs.get("Cask number") or attrs.get("Casknumber"),
        "vintage": attrs.get("Vintage"),
        "bottled": attrs.get("Bottled"),
        "category": attrs.get("Category"),
        "rating": rating,
        "votes": votes,
        "image_url": image_url,
        "url": f"{BASE_URL}/{wbid}",
    }


async def scrape_whisky(page: Page, wbid: int) -> dict | None:
    url = f"{BASE_URL}/{wbid}"
    try:
        response = await page.goto(url, wait_until="commit", timeout=60000)
        if response and response.status == 404:
            return None

        # Wait for real content (Cloudflare may solve JS challenge first)
        try:
            await page.wait_for_selector("h1", timeout=30000)
        except Exception:
            log.warning("WBID %d: page didn't load (status %s)", wbid, response.status if response else "?")
            return None
        await asyncio.sleep(1)

        html = await page.content()
        return parse_whisky_page(html, wbid)

    except Exception as e:
        log.error("WBID %d: %s", wbid, e)
        return None


async def run_detail_crawler(
    start_wbid: int | None = None,
    end_wbid: int = 400000,
    delay_min: float = 2.0,
    delay_max: float = 5.0,
    headless: bool = True,
):
    """Phase 2: Crawl individual whisky pages for full details."""
    conn = get_connection()
    init_db(conn)

    if start_wbid is None:
        start_wbid = get_last_wbid(conn) + 1

    log.info("Detail crawler: WBID %d to %d", start_wbid, end_wbid)

    scraped = 0
    skipped = 0

    async with async_playwright() as p:
        browser, context, page = await _create_browser(p, headless)
        await _warmup(page)

        try:
            for wbid in range(start_wbid, end_wbid + 1):
                data = await scrape_whisky(page, wbid)

                if data:
                    save_whisky(conn, data)
                    scraped += 1
                    log.info(
                        "[%d] %s - %s (%s)",
                        wbid,
                        data.get("distillery", "?"),
                        data["name"],
                        data.get("strength", "?"),
                    )
                else:
                    skipped += 1
                    log.debug("[%d] Not found / skipped", wbid)

                set_last_wbid(conn, wbid)

                if (wbid - start_wbid + 1) % 100 == 0:
                    log.info(
                        "Progress: WBID %d | scraped=%d skipped=%d",
                        wbid, scraped, skipped,
                    )

                delay = random.uniform(delay_min, delay_max)
                await asyncio.sleep(delay)

        except KeyboardInterrupt:
            log.info("Interrupted at WBID %d", wbid)
        finally:
            await browser.close()
            conn.close()

    log.info("Done. Scraped: %d, Skipped: %d", scraped, skipped)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _create_browser(p, headless: bool) -> tuple[Browser, BrowserContext, Page]:
    browser = await p.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    stealth = Stealth()
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )
    await stealth.apply_stealth_async(context)
    page = await context.new_page()
    return browser, context, page


async def _warmup(page: Page):
    log.info("Warming up: visiting homepage...")
    try:
        await page.goto("https://www.whiskybase.com", wait_until="load", timeout=60000)
        await asyncio.sleep(5)
        try:
            accept_btn = page.locator(
                "button:has-text('Accept'), button:has-text('agree'), "
                ".cookie-accept, #onetrust-accept-btn-handler"
            )
            if await accept_btn.count() > 0:
                await accept_btn.first.click()
                await asyncio.sleep(1)
        except Exception:
            pass
    except Exception as e:
        log.warning("Homepage warmup failed: %s", e)
