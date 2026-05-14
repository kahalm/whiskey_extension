import asyncio
import logging
import random
import re
import string

from bs4 import BeautifulSoup
import nodriver as uc

from scraper.db import (
    get_connection, init_db, save_whisky, save_whisky_basic,
    get_last_wbid, set_last_wbid, get_whisky_count,
    get_search_state, set_search_state, has_wbid,
    get_releases_state, set_releases_state,
    get_unscraped_wbids,
)

log = logging.getLogger(__name__)

BASE_URL = "https://www.whiskybase.com/whiskies/whisky"
SEARCH_URL = "https://www.whiskybase.com/search-v1/whisky"
RELEASES_URL = "https://www.whiskybase.com/whiskies/new-releases"


# ---------------------------------------------------------------------------
# Phase 1: Bulk-collect WBIDs + basic data via search
# ---------------------------------------------------------------------------

SEARCH_QUERIES = (
    [a + b for a in string.ascii_lowercase for b in string.ascii_lowercase]
    + [str(y) for y in range(1900, 2027)]
    + [str(n) for n in range(1, 100)]
)

RESULT_LIMIT = 400   # expand query if results >= this
MAX_QUERY_DEPTH = 5  # max expansion depth (e.g. 'abcde')


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


async def search_whiskies(page, query: str) -> list[dict]:
    """Fetch search results for a query and return parsed whisky data."""
    url = f"{SEARCH_URL}?q={query}"
    try:
        await page.get(url)
        await page.select("table.whiskytable, .no-results", timeout=15)
        await asyncio.sleep(1)
        html = await page.get_content()
        return parse_search_results(html)
    except Exception as e:
        log.error("Search '%s': %s", query, e)
        return []


async def _search_recursive(page, conn, query, last_query, delay_min, delay_max):
    """Search for a query, recursively expanding with [a-z] if results hit the limit."""
    # Skip logic for resume
    if last_query:
        if query == last_query:
            return 0
        if query < last_query and not last_query.startswith(query):
            return 0

    # If resuming into an expanded parent, skip fetch and go straight to children
    resuming = last_query and last_query.startswith(query) and query != last_query

    new_count = 0
    result_count = 0

    if not resuming:
        results = await search_whiskies(page, query)
        result_count = len(results)
        for item in results:
            if not has_wbid(conn, item["wbid"]):
                save_whisky_basic(conn, item)
                new_count += 1
        await asyncio.sleep(random.uniform(delay_min, delay_max))

    if resuming or (result_count >= RESULT_LIMIT and len(query) < MAX_QUERY_DEPTH):
        if not resuming:
            log.info(
                "'%s': %d results, %d new — expanding to %s[a-z]",
                query, result_count, new_count, query,
            )
        for c in string.ascii_lowercase:
            new_count += await _search_recursive(
                page, conn, query + c, last_query, delay_min, delay_max
            )
        set_search_state(conn, query)
    elif not resuming:
        log.info(
            "'%s': %d results, %d new (total in DB: %d)",
            query, result_count, new_count, get_whisky_count(conn),
        )
        set_search_state(conn, query)

    return new_count


async def run_search_collector(
    delay_min: float = 2.0,
    delay_max: float = 5.0,
    headless: bool = True,
):
    """Phase 1: Collect WBIDs and basic data via search queries."""
    conn = get_connection()
    init_db(conn)

    last_query = get_search_state(conn)
    log.info(
        "Search collector: %d base queries, %d whiskies in DB%s",
        len(SEARCH_QUERIES),
        get_whisky_count(conn),
        f" (resuming from '{last_query}')" if last_query else "",
    )

    total_new = 0

    browser = await _create_browser(headless)
    page = await _warmup(browser)

    try:
        for query in SEARCH_QUERIES:
            total_new += await _search_recursive(
                page, conn, query, last_query, delay_min, delay_max
            )
    except KeyboardInterrupt:
        log.info("Interrupted.")
    finally:
        browser.stop()
        conn.close()

    log.info("Search collector done. %d new whiskies added.", total_new)


# ---------------------------------------------------------------------------
# Phase 1b: Collect WBIDs via new-releases filter (year by year)
# ---------------------------------------------------------------------------

RELEASES_START_YEAR = 2026
RELEASES_END_YEAR = 1870


def parse_releases_table(html: str) -> list[dict]:
    """Parse new-releases table. Different column layout than search results.

    Columns: [0]img  [1]Name(link)  [2]Stated Age  [3]Strength
             [4]Size  [5]Bottled  [6]Cask number  [7]Rating
             [8]Versions  [9]Whisky listings  [10]empty
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    table = soup.select_one("table.whiskytable")
    if not table:
        return results

    for row in table.find_all("tr")[1:]:  # skip header
        cells = row.find_all("td")
        if len(cells) < 8:
            continue

        # Name + WBID from cell [1]
        name_cell = cells[1]
        link = name_cell.find("a", href=True)
        if not link or "/whiskies/whisky/" not in link["href"]:
            continue

        match = re.search(r"/whiskies/whisky/(\d+)", link["href"])
        if not match:
            continue

        wbid = int(match.group(1))
        name = link.get_text(strip=True)

        # Image from cell [0]
        image_url = None
        img_link = cells[0].find("a", href=True)
        if img_link:
            src = img_link.get("href", "")
            if src and "default" not in src:
                image_url = src

        age = cells[2].get_text(strip=True) or None
        strength = cells[3].get_text(strip=True) or None
        size = cells[4].get_text(strip=True) or None
        bottled = cells[5].get_text(strip=True) or None
        cask_number = cells[6].get_text(strip=True) or None
        rating_text = cells[7].get_text(strip=True) or None

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
            "rating": rating,
            "image_url": image_url,
            "url": f"{BASE_URL}/{wbid}",
        })

    return results


async def _fetch_releases_page(page, url: str) -> tuple[list[dict], str | None]:
    """Fetch a single releases page, return (results, next_page_url | None)."""
    try:
        await page.get(url)

        # Wait for real content (not Cloudflare challenge page)
        el = await page.select("table.whiskytable, .no-results", timeout=15)
        if el is None:
            log.info("Turnstile detected on %s — solving...", url)
            solved = await _solve_turnstile(page)
            if solved:
                el = await page.select("table.whiskytable, .no-results", timeout=15)
            if el is None:
                log.warning("Page didn't load after Turnstile attempt: %s", url)
                return [], None
        await asyncio.sleep(1)

        html = await page.get_content()
        results = parse_releases_table(html)

        # Check for next page link
        soup = BeautifulSoup(html, "html.parser")
        next_link = soup.select_one("a.next, li.next a, a[rel='next']")
        next_url = None
        if next_link and next_link.get("href"):
            href = next_link["href"]
            if href.startswith("/"):
                next_url = "https://www.whiskybase.com" + href
            elif href.startswith("http"):
                next_url = href

        return results, next_url

    except Exception as e:
        log.error("Releases page %s: %s", url, e)
        return [], None


async def _run_releases_pass(page, conn, votes, delay_min, delay_max):
    """Single pass over all years for a given votes filter. Returns count of new whiskies."""
    filter_key = f"votes={votes}" if votes != "" else "default"
    last_year = get_releases_state(conn, filter_key)
    log.info(
        "Releases pass [%s]: years %d→%d, %d whiskies in DB%s",
        filter_key, RELEASES_START_YEAR, RELEASES_END_YEAR,
        get_whisky_count(conn),
        f" (resuming after year {last_year})" if last_year else "",
    )

    total_new = 0

    for year in range(RELEASES_START_YEAR, RELEASES_END_YEAR - 1, -1):
        if last_year and year >= last_year:
            continue

        url = f"{RELEASES_URL}?bottle_date_year={year}"
        if votes != "":
            url += f"&votes={votes}"
        page_num = 1
        year_new = 0

        while url:
            results, next_url = await _fetch_releases_page(page, url)

            for item in results:
                if not has_wbid(conn, item["wbid"]):
                    save_whisky_basic(conn, item)
                    year_new += 1

            log.info(
                "Year %d page %d: %d results, %d new",
                year, page_num, len(results), year_new,
            )

            url = next_url
            page_num += 1
            await asyncio.sleep(random.uniform(delay_min, delay_max))

        total_new += year_new
        set_releases_state(conn, year, filter_key)
        log.info(
            "Year %d done: %d new (total in DB: %d)",
            year, year_new, get_whisky_count(conn),
        )

    log.info("Pass [%s] done. %d new whiskies.", filter_key, total_new)
    return total_new


async def run_releases_collector(
    delay_min: float = 2.0,
    delay_max: float = 5.0,
    headless: bool = True,
):
    """Phase 1b: Collect WBIDs via new-releases filter, iterating years 2026→1870.
    Runs two passes: first rated whiskies, then unrated (votes=0)."""
    conn = get_connection()
    init_db(conn)

    total_new = 0

    browser = await _create_browser(headless)
    page = await _warmup(browser)

    try:
        total_new += await _run_releases_pass(page, conn, "", delay_min, delay_max)
        total_new += await _run_releases_pass(page, conn, "0", delay_min, delay_max)
    except KeyboardInterrupt:
        log.info("Interrupted.")
    except Exception as e:
        log.error("Browser error: %s", e)
    finally:
        browser.stop()
        conn.close()

    log.info("Releases collector done. %d new whiskies added.", total_new)


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

    # Count shop listings
    shop_count = 0
    no_listings = soup.find(string=re.compile(r"No active listings", re.IGNORECASE))
    if no_listings:
        shop_count = 0
    else:
        # Count rows in prices section
        prices_section = soup.select("#tab-prices tr, .shop-listing")
        if prices_section:
            shop_count = max(0, len(prices_section) - 1)  # minus header row

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
        "shop_price": attrs.get("Last average shop price"),
        "shop_count": shop_count,
        "rating": rating,
        "votes": votes,
        "image_url": image_url,
        "url": f"{BASE_URL}/{wbid}",
    }


async def scrape_whisky(page, wbid: int) -> dict | None:
    url = f"{BASE_URL}/{wbid}"
    try:
        await page.get(url)

        # Wait for real content (not Cloudflare challenge page)
        el = await page.select("dl, .whisky-header, .block-whisky", timeout=15)
        if el is None:
            log.debug("WBID %d: Turnstile detected — solving...", wbid)
            solved = await _solve_turnstile(page)
            if solved:
                el = await page.select("dl, .whisky-header, .block-whisky", timeout=15)
            if el is None:
                log.warning("WBID %d: page didn't load", wbid)
                return None
        await asyncio.sleep(1)

        html = await page.get_content()
        return parse_whisky_page(html, wbid)

    except Exception as e:
        log.error("WBID %d: %s", wbid, e)
        return None


async def run_detail_crawler(
    delay_min: float = 2.0,
    delay_max: float = 5.0,
    headless: bool = True,
):
    """Phase 2: Crawl individual whisky pages for full details.
    Only processes WBIDs already in the DB that haven't been detail-scraped."""
    conn = get_connection()
    init_db(conn)

    total_in_db = get_whisky_count(conn)
    wbids = get_unscraped_wbids(conn)
    already_done = total_in_db - len(wbids)
    log.info("Detail crawler: %d/%d still to scrape", len(wbids), total_in_db)

    if not wbids:
        conn.close()
        return

    scraped = 0
    skipped = 0

    browser = await _create_browser(headless)
    page = await _warmup(browser)

    try:
        for i, wbid in enumerate(wbids):
            data = await scrape_whisky(page, wbid)

            if data:
                save_whisky(conn, data)
                scraped += 1
                log.info(
                    "[%d/%d] %s - %s (%s) rating=%s",
                    already_done + scraped, total_in_db,
                    data.get("distillery", "?"),
                    data["name"],
                    data.get("strength", "?"),
                    data.get("rating", "?"),
                )
            else:
                skipped += 1
                log.debug("[%d/%d] WBID %d not found / skipped", i + 1, len(wbids), wbid)

            if (i + 1) % 100 == 0:
                log.info(
                    "Progress: %d/%d | scraped=%d skipped=%d",
                    i + 1, len(wbids), scraped, skipped,
                )

            delay = random.uniform(delay_min, delay_max)
            await asyncio.sleep(delay)

    except KeyboardInterrupt:
        log.info("Interrupted at %d/%d", i + 1, len(wbids))
    finally:
        browser.stop()
        conn.close()

    log.info("Done. Scraped: %d, Skipped: %d", scraped, skipped)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _create_browser(headless: bool = True):
    """Start a Chrome browser via nodriver (undetected)."""
    browser = await uc.start(
        headless=headless,
        browser_args=[
            "--no-sandbox",
            "--window-size=1920,1080",
            "--enable-unsafe-swiftshader",
            "--use-gl=angle",
            "--use-angle=swiftshader",
        ],
    )
    return browser


_STEALTH_JS = """\
// --- deviceMemory (missing in Docker → bot signal) ---
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8, configurable: true});

// --- WebGL renderer (SwiftShader → bot signal) ---
(function() {
    const VENDOR = 'Google Inc. (NVIDIA)';
    const RENDERER = 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)';
    function patchGetParameter(proto) {
        const orig = proto.getParameter;
        proto.getParameter = function(param) {
            if (param === 37445) return VENDOR;
            if (param === 37446) return RENDERER;
            return orig.call(this, param);
        };
    }
    if (typeof WebGLRenderingContext !== 'undefined')
        patchGetParameter(WebGLRenderingContext.prototype);
    if (typeof WebGL2RenderingContext !== 'undefined')
        patchGetParameter(WebGL2RenderingContext.prototype);
})();

// --- Canvas fingerprint noise (Xvfb renders differently than real GPU) ---
(function() {
    const origToBlob = HTMLCanvasElement.prototype.toBlob;
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    function addNoise(canvas) {
        try {
            const ctx = canvas.getContext('2d');
            if (!ctx || canvas.width < 2 || canvas.height < 2) return;
            const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const d = img.data;
            // Deterministic subtle noise based on pixel position
            for (let i = 0; i < d.length; i += 4) {
                d[i]   = d[i]   ^ ((i * 31) & 1);
                d[i+1] = d[i+1] ^ ((i * 37) & 1);
            }
            ctx.putImageData(img, 0, 0);
        } catch(e) {}
    }
    HTMLCanvasElement.prototype.toDataURL = function() {
        addNoise(this);
        return origToDataURL.apply(this, arguments);
    };
    HTMLCanvasElement.prototype.toBlob = function() {
        addNoise(this);
        return origToBlob.apply(this, arguments);
    };
})();

// --- AudioContext fingerprint noise ---
(function() {
    if (typeof AudioContext === 'undefined' && typeof webkitAudioContext === 'undefined') return;
    const AC = typeof AudioContext !== 'undefined' ? AudioContext : webkitAudioContext;
    const origGetFloatFreqData = AnalyserNode.prototype.getFloatFrequencyData;
    AnalyserNode.prototype.getFloatFrequencyData = function(array) {
        origGetFloatFreqData.call(this, array);
        for (let i = 0; i < array.length; i++) {
            array[i] += 0.1 * ((i * 13) % 7 - 3);
        }
    };
})();
"""


async def _inject_stealth(page):
    """Inject stealth overrides that run before any page script."""
    try:
        await page.send(uc.cdp.page.enable())
        await page.send(uc.cdp.page.add_script_to_evaluate_on_new_document(source=_STEALTH_JS))
        log.debug("Stealth JS injected")
    except Exception as e:
        log.warning("Could not inject stealth JS: %s", e)


async def _check_ip(browser):
    """Log public IP address for verification."""
    try:
        page = await browser.get("https://api.ipify.org")
        await asyncio.sleep(2)
        html = await page.get_content()
        from bs4 import BeautifulSoup as _BS
        ip = _BS(html, "html.parser").get_text(strip=True)
        log.info("Public IP: %s", ip)
    except Exception as e:
        log.warning("Could not determine public IP: %s", e)


async def _solve_turnstile(page, max_wait: int = 30):
    """Wait for Cloudflare challenge to resolve, clicking Turnstile checkbox if it appears.

    The challenge page ("Just a moment...") can resolve in two ways:
    1. JS challenge passes silently → page redirects automatically
    2. Turnstile widget appears → needs a click on the checkbox
    """
    clicked = False
    for tick in range(max_wait // 2):
        await asyncio.sleep(2)

        # Check if page title changed (challenge passed)
        title = await page.evaluate("document.title")
        if title and "just a moment" not in title.lower():
            log.info("Cloudflare challenge passed (title: %s)", title)
            return True

        # Look for Turnstile iframe
        iframe_info = await page.evaluate("""
            (() => {
                const frames = document.querySelectorAll("iframe");
                for (const f of frames) {
                    const src = f.src || "";
                    if (src.includes("challenges.cloudflare.com") || src.includes("turnstile")) {
                        const rect = f.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return {x: rect.x, y: rect.y, w: rect.width, h: rect.height};
                        }
                    }
                }
                const widget = document.querySelector("[id^='cf-chl-widget']");
                if (widget) {
                    const iframe = widget.querySelector("iframe");
                    if (iframe) {
                        const rect = iframe.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return {x: rect.x, y: rect.y, w: rect.width, h: rect.height};
                        }
                    }
                }
                return null;
            })()
        """)

        if not iframe_info:
            # No iframe yet — JS challenge may still be running
            if tick % 3 == 0:
                log.debug("Waiting for Cloudflare challenge... (%ds)", (tick + 1) * 2)
            continue

        log.info(
            "Turnstile iframe at (%.0f, %.0f) %.0fx%.0f — clicking...",
            iframe_info["x"], iframe_info["y"],
            iframe_info["w"], iframe_info["h"],
        )

        # Checkbox is near left edge of iframe, vertically centered
        cx = int(iframe_info["x"]) + 35
        cy = int(iframe_info["y"]) + int(iframe_info["h"]) // 2

        # Move mouse, then click via CDP
        await page.send(uc.cdp.input_.dispatch_mouse_event(
            type_="mouseMoved", x=cx, y=cy,
        ))
        await asyncio.sleep(random.uniform(0.3, 0.7))
        await page.send(uc.cdp.input_.dispatch_mouse_event(
            type_="mousePressed", x=cx, y=cy,
            button=uc.cdp.input_.MouseButton.LEFT, click_count=1,
        ))
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await page.send(uc.cdp.input_.dispatch_mouse_event(
            type_="mouseReleased", x=cx, y=cy,
            button=uc.cdp.input_.MouseButton.LEFT, click_count=1,
        ))
        clicked = True

        # Give Cloudflare time to verify after click
        await asyncio.sleep(5)

    log.warning("Cloudflare challenge did not resolve within %ds (clicked=%s)", max_wait, clicked)
    return False


async def _warmup(browser):
    """Visit homepage to establish cookies and pass Cloudflare challenge."""
    await _check_ip(browser)
    log.info("Warming up: visiting homepage...")
    page = await browser.get("about:blank")
    await _inject_stealth(page)
    await page.get("https://www.whiskybase.com")
    await asyncio.sleep(5)

    # Solve Turnstile challenge if present
    await _solve_turnstile(page)

    try:
        accept_btn = await page.find("Accept", best_match=True, timeout=5)
        if accept_btn:
            await accept_btn.click()
            await asyncio.sleep(1)
    except Exception:
        pass
    return page
