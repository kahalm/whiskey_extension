"""Tests for HTML parsers — no browser or DB needed."""

from scraper.crawler import parse_search_results, parse_releases_table, parse_whisky_page


# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

SEARCH_HTML = """
<table class="whiskytable">
  <tr><th>#</th><th></th><th>Name</th><th>Age</th><th>Strength</th>
      <th>Size</th><th>Bottled</th><th>Cask#</th><th>Barcode</th><th>Rating</th><th></th></tr>
  <tr>
    <td>1</td>
    <td><a href="https://img.whiskybase.com/photo1.jpg">img</a></td>
    <td><a href="/whiskies/whisky/12345/laphroaig-10">Laphroaig 10yo</a></td>
    <td>10</td>
    <td>43.0 % Vol.</td>
    <td>700 ml</td>
    <td>2020</td>
    <td>123</td>
    <td>5012345678</td>
    <td>88.50</td>
    <td></td>
  </tr>
  <tr>
    <td>2</td>
    <td></td>
    <td><a href="/whiskies/whisky/67890/ardbeg-uigeadail">Ardbeg Uigeadail</a></td>
    <td></td>
    <td>54.2 % Vol.</td>
    <td>700 ml</td>
    <td>2021</td>
    <td></td>
    <td></td>
    <td>92.10</td>
    <td></td>
  </tr>
</table>
"""

SEARCH_HTML_EMPTY = """<div class="no-results">No results found</div>"""

SEARCH_HTML_NO_TABLE = """<h1>Whiskybase</h1><p>Nothing here</p>"""

RELEASES_HTML = """
<table class="whiskytable table table-clickable compositor table-sortable">
  <tr><th></th><th>Name</th><th>Stated Age</th><th>Strength</th>
      <th>Size</th><th>Bottled</th><th>Cask number</th><th>Rating</th>
      <th>Versions</th><th>Whisky listings</th><th></th></tr>
  <tr>
    <td><a href="https://img.whiskybase.com/photo2.jpg">img</a></td>
    <td><a href="/whiskies/whisky/11111/bowmore-15">Bowmore 15yo</a></td>
    <td>15</td>
    <td>43.0 % Vol.</td>
    <td>700 ml</td>
    <td>2025</td>
    <td>456</td>
    <td>90.00</td>
    <td></td>
    <td></td>
    <td></td>
  </tr>
  <tr>
    <td></td>
    <td><a href="/whiskies/whisky/22222/caol-ila-12">Caol Ila 12yo</a></td>
    <td>12</td>
    <td>43.0 % Vol.</td>
    <td>700 ml</td>
    <td>2024</td>
    <td></td>
    <td></td>
    <td></td>
    <td></td>
    <td></td>
  </tr>
  <tr>
    <td></td>
    <td><a href="/whiskies/whisky/33333/talisker-10">Talisker 10yo</a></td>
    <td>10</td>
    <td>45.8 % Vol.</td>
    <td>700 ml</td>
    <td>2025</td>
    <td></td>
    <td>87.50</td>
    <td></td>
    <td></td>
    <td></td>
  </tr>
</table>
"""

RELEASES_HTML_EMPTY = """
<table class="whiskytable"><tr><th></th><th>Name</th></tr></table>
"""

DETAIL_HTML_WITH_SHOPS = """
<h1>Laphroaig 10yo</h1>
<dl>
  <dt>Category</dt><dd>Single Malt Scotch Whisky</dd>
  <dt>Distillery</dt><dd>Laphroaig</dd>
  <dt>District</dt><dd>Islay</dd>
  <dt>Country</dt><dd>Scotland</dd>
  <dt>Stated Age</dt><dd>10 years old</dd>
  <dt>Strength</dt><dd>43.0 % Vol.</dd>
  <dt>Size</dt><dd>700 ml</dd>
  <dt>Bottler</dt><dd>Original Bottling</dd>
  <dt>Bottling series</dt><dd>Core Range</dd>
  <dt>Cask Type</dt><dd>Bourbon</dd>
  <dt>Vintage</dt><dd>2010</dd>
  <dt>Bottled</dt><dd>2020</dd>
  <dt>Last average shop price</dt><dd>€ 35.00</dd>
</dl>
<span class="votes-rating-current">88.50</span>
<dd class="votes-count">1,234</dd>
<a class="whisky-image-big"><img src="https://img.whiskybase.com/big.jpg"></a>
<div id="tab-prices">
  <table>
    <tr><th>Shop</th><th>Price</th></tr>
    <tr><td>Shop A</td><td>€ 33.00</td></tr>
    <tr><td>Shop B</td><td>€ 37.00</td></tr>
  </table>
</div>
"""

DETAIL_HTML_NO_SHOPS = """
<h1>Rare Old Whisky</h1>
<dl>
  <dt>Category</dt><dd>Single Malt Scotch Whisky</dd>
  <dt>Distillery</dt><dd>Brora</dd>
  <dt>Country</dt><dd>Scotland</dd>
  <dt>Strength</dt><dd>54.9 % Vol.</dd>
</dl>
<span class="votes-rating-current">95.00</span>
<dd class="votes-count">42</dd>
<div id="tab-prices">No active listings.</div>
"""

DETAIL_HTML_NOT_FOUND = """<h1>Page not found</h1><p>Sorry</p>"""

DETAIL_HTML_NO_ATTRS = """<h1>Laphroaig</h1><p>No details</p>"""


# ---------------------------------------------------------------------------
# parse_search_results
# ---------------------------------------------------------------------------

class TestParseSearchResults:
    def test_two_results(self):
        results = parse_search_results(SEARCH_HTML)
        assert len(results) == 2

        r1 = results[0]
        assert r1["wbid"] == 12345
        assert r1["name"] == "Laphroaig 10yo"
        assert r1["age"] == "10"
        assert r1["strength"] == "43.0 % Vol."
        assert r1["size"] == "700 ml"
        assert r1["bottled"] == "2020"
        assert r1["cask_number"] == "123"
        assert r1["barcode"] == "5012345678"
        assert r1["rating"] == 88.50
        assert r1["image_url"] == "https://img.whiskybase.com/photo1.jpg"
        assert "/whiskies/whisky/12345" in r1["url"]

        r2 = results[1]
        assert r2["wbid"] == 67890
        assert r2["name"] == "Ardbeg Uigeadail"
        assert r2["age"] is None
        assert r2["cask_number"] is None
        assert r2["barcode"] is None
        assert r2["rating"] == 92.10

    def test_empty_page(self):
        assert parse_search_results(SEARCH_HTML_EMPTY) == []

    def test_no_table(self):
        assert parse_search_results(SEARCH_HTML_NO_TABLE) == []


# ---------------------------------------------------------------------------
# parse_releases_table
# ---------------------------------------------------------------------------

class TestParseReleasesTable:
    def test_three_results(self):
        results = parse_releases_table(RELEASES_HTML)
        assert len(results) == 3

        r1 = results[0]
        assert r1["wbid"] == 11111
        assert r1["name"] == "Bowmore 15yo"
        assert r1["age"] == "15"
        assert r1["strength"] == "43.0 % Vol."
        assert r1["bottled"] == "2025"
        assert r1["cask_number"] == "456"
        assert r1["rating"] == 90.00
        assert r1["image_url"] == "https://img.whiskybase.com/photo2.jpg"

        r2 = results[1]
        assert r2["wbid"] == 22222
        assert r2["name"] == "Caol Ila 12yo"
        assert r2["rating"] is None
        assert r2["image_url"] is None

        r3 = results[2]
        assert r3["wbid"] == 33333
        assert r3["rating"] == 87.50

    def test_empty_table(self):
        assert parse_releases_table(RELEASES_HTML_EMPTY) == []

    def test_no_table(self):
        assert parse_releases_table("<html><body>nothing</body></html>") == []


# ---------------------------------------------------------------------------
# parse_whisky_page (detail)
# ---------------------------------------------------------------------------

class TestParseWhiskyPage:
    def test_full_detail_with_shops(self):
        data = parse_whisky_page(DETAIL_HTML_WITH_SHOPS, 12345)
        assert data is not None
        assert data["wbid"] == 12345
        assert data["name"] == "Laphroaig 10yo"
        assert data["distillery"] == "Laphroaig"
        assert data["district"] == "Islay"
        assert data["country"] == "Scotland"
        assert data["age"] == "10 years old"
        assert data["strength"] == "43.0 % Vol."
        assert data["size"] == "700 ml"
        assert data["bottler"] == "Original Bottling"
        assert data["bottling_serie"] == "Core Range"
        assert data["cask_type"] == "Bourbon"
        assert data["vintage"] == "2010"
        assert data["bottled"] == "2020"
        assert data["category"] == "Single Malt Scotch Whisky"
        assert data["shop_price"] == "€ 35.00"
        assert data["shop_count"] == 2
        assert data["rating"] == 88.50
        assert data["votes"] == 1234
        assert data["image_url"] == "https://img.whiskybase.com/big.jpg"

    def test_no_active_listings(self):
        data = parse_whisky_page(DETAIL_HTML_NO_SHOPS, 99999)
        assert data is not None
        assert data["shop_count"] == 0
        assert data["shop_price"] is None
        assert data["distillery"] == "Brora"
        assert data["rating"] == 95.00
        assert data["votes"] == 42

    def test_not_found_page(self):
        assert parse_whisky_page(DETAIL_HTML_NOT_FOUND, 1) is None

    def test_no_attributes(self):
        assert parse_whisky_page(DETAIL_HTML_NO_ATTRS, 1) is None
