"""Live integration tests — starts a real browser and hits whiskybase.com.

Run with: python -m pytest tests/test_live.py -v -s
These tests may fail due to Cloudflare blocking — marked as xfail.
Requires: playwright browsers installed
"""

import asyncio
import pytest

from scraper.crawler import (
    _create_browser, _warmup, _fetch_releases_page,
    scrape_whisky,
)

CF_REASON = "May fail due to Cloudflare blocking"


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def browser_page(event_loop):
    """Start browser once for all tests in this module."""
    from playwright.async_api import async_playwright

    pw = None
    browser = None

    async def setup():
        nonlocal pw, browser
        pw = await async_playwright().start()
        browser, context, page = await _create_browser(pw, headless=True)
        await _warmup(page)
        return page

    page = event_loop.run_until_complete(setup())
    yield page

    async def teardown():
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

    event_loop.run_until_complete(teardown())


# ---------------------------------------------------------------------------
# Releases list tests
# ---------------------------------------------------------------------------

class TestLiveReleases:
    @pytest.mark.xfail(reason=CF_REASON, strict=False)
    def test_releases_2024(self, browser_page, event_loop):
        """2024 should have many releases."""
        url = "https://www.whiskybase.com/whiskies/new-releases?bottle_date_year=2024"
        results, next_url = event_loop.run_until_complete(
            _fetch_releases_page(browser_page, url)
        )
        assert len(results) > 1, f"Expected >1 results for 2024, got {len(results)}"
        r = results[0]
        assert r["wbid"] > 0
        assert r["name"]

    @pytest.mark.xfail(reason=CF_REASON, strict=False)
    def test_releases_2020(self, browser_page, event_loop):
        """2020 should also have results."""
        url = "https://www.whiskybase.com/whiskies/new-releases?bottle_date_year=2020"
        results, next_url = event_loop.run_until_complete(
            _fetch_releases_page(browser_page, url)
        )
        assert len(results) > 1, f"Expected >1 results for 2020, got {len(results)}"


# ---------------------------------------------------------------------------
# Detail page tests
# ---------------------------------------------------------------------------

class TestLiveDetail:
    @pytest.mark.xfail(reason=CF_REASON, strict=False)
    def test_detail_port_charlotte(self, browser_page, event_loop):
        """WBID 58740 (Port Charlotte PC9) — known existing whisky."""
        data = event_loop.run_until_complete(
            scrape_whisky(browser_page, 58740)
        )
        assert data is not None, "Expected data for WBID 58740"
        assert data["wbid"] == 58740
        assert data["name"]
        assert data["distillery"]
        assert data["country"]
        assert data["category"]

    @pytest.mark.xfail(reason=CF_REASON, strict=False)
    def test_detail_classic(self, browser_page, event_loop):
        """WBID 1 — one of the earliest entries."""
        data = event_loop.run_until_complete(
            scrape_whisky(browser_page, 1)
        )
        assert data is not None, "Expected data for WBID 1"
        assert data["wbid"] == 1
        assert data["name"]
