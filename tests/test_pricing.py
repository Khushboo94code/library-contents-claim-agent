"""Pricing module tests — run entirely offline.

The network seam (:data:`backend.pricing.price._FETCHER`) is monkeypatched to a
fake that returns canned Amazon-search HTML (success) or ``None`` (failure).
These tests pin the two load-bearing invariants:

* a successful fetch maps to the right dict shape with ``amount`` set, a real
  product ``url``, and ``retrieved_at`` populated; and
* a failed/empty fetch yields ``amount is None`` and *no fabricated* url/source.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from backend.pricing import price as pricing


# --------------------------------------------------------------------------- #
# Fixtures: canned Amazon search-results HTML.
# --------------------------------------------------------------------------- #

BOOK_HTML = """
<html><body>
<div data-asin="B00AAA111" class="s-result-item">
  <h2 class="a-text-normal"><span>The Pragmatic Programmer by Andrew Hunt</span></h2>
  <span class="a-price"><span class="a-offscreen">1,299.00</span></span>
  <span class="a-price"><span class="a-offscreen">899.00</span></span>
</div>
<div data-asin="B00BBB222" class="s-result-item">
  <h2 class="a-text-normal"><span>Totally Unrelated Cookbook</span></h2>
  <span class="a-price"><span class="a-offscreen">499.00</span></span>
</div>
</body></html>
"""

ITEM_HTML = """
<html><body>
<div data-asin="C00111" class="s-result-item">
  <h2 class="a-text-normal"><span>Stainless Steel Table Lamp Desk Light</span></h2>
  <span class="a-price"><span class="a-offscreen">1,500.00</span></span>
</div>
<div data-asin="C00222" class="s-result-item">
  <h2 class="a-text-normal"><span>Steel Desk Lamp LED Light</span></h2>
  <span class="a-price"><span class="a-offscreen">2,300.00</span></span>
</div>
<div data-asin="C00333" class="s-result-item">
  <h2 class="a-text-normal"><span>Steel Lamp Desk Light Premium</span></h2>
  <span class="a-price"><span class="a-offscreen">900.00</span></span>
</div>
</body></html>
"""


@pytest.fixture(autouse=True)
def restore_fetcher():
    """Each test restores the real fetchers afterwards so nothing leaks. AbeBooks is
    stubbed to 'no result' by default so book tests stay offline unless a test opts in."""
    original = pricing._FETCHER
    original_abe = pricing._ABE_FETCHER
    pricing.set_abe_fetcher(lambda url: None)
    yield
    pricing.set_fetcher(original)
    pricing.set_abe_fetcher(original_abe)


# --------------------------------------------------------------------------- #
# Clean import even with no network / no optional deps.
# --------------------------------------------------------------------------- #


def test_module_imports_cleanly():
    import backend.pricing.price  # noqa: F401


# --------------------------------------------------------------------------- #
# Book: successful fetch.
# --------------------------------------------------------------------------- #


def test_price_book_success_maps_shape_and_amount():
    captured = {}

    def fake_fetch(url, country="IN"):
        captured["url"] = url
        captured["country"] = country
        return BOOK_HTML

    pricing.set_fetcher(fake_fetch)
    out = pricing.price_book("The Pragmatic Programmer", author="Andrew Hunt", country="IN")

    # Hit the correct marketplace.
    assert "amazon.in" in captured["url"]
    assert captured["country"] == "IN"

    rc = out["replacement_cost"]
    assert set(rc) == {"amount", "source", "url", "retrieved_at", "converted"}
    assert rc["amount"] == 899.00  # lowest headline price on the best-matched card
    assert rc["source"] == "amazon"
    assert "B00AAA111" in rc["url"]  # points at the matched product
    assert rc["retrieved_at"] == date.today().isoformat()
    assert rc["converted"] is False

    uv = out["used_value"]
    assert set(uv) == {"amount", "source", "url", "retrieved_at", "condition_assumed"}
    # Used value is NOT taken from a search card (the second price is the MRP, not a
    # used offer) — it stays None here; a real used figure needs the product page.
    assert uv["amount"] is None


def test_price_book_picks_best_match_not_first_div():
    """Even if an unrelated card appears, the title match wins."""
    pricing.set_fetcher(lambda url, country="IN": BOOK_HTML)
    out = pricing.price_book("Pragmatic Programmer", author="Hunt")
    assert "B00AAA111" in out["replacement_cost"]["url"]


# --------------------------------------------------------------------------- #
# Book: failure / empty fetch -> None, no fabrication.
# --------------------------------------------------------------------------- #


def test_price_book_blocked_returns_none_no_fabrication():
    pricing.set_fetcher(lambda url, country="IN": None)  # blocked / timeout
    out = pricing.price_book("Some Title", author="Someone")

    rc = out["replacement_cost"]
    assert rc["amount"] is None
    assert rc["source"] == ""
    assert rc["url"] == ""  # NO fabricated url
    assert rc["retrieved_at"] == date.today().isoformat()

    uv = out["used_value"]
    assert uv["amount"] is None
    assert uv["url"] == ""
    assert uv["source"] == ""


def test_price_book_no_match_returns_none():
    # HTML with a card that shares no tokens with the query.
    html = (
        '<div data-asin="Z1"><h2 class="a-text-normal"><span>zzzz qqqq</span></h2>'
        '<span class="a-offscreen">10.00</span></div>'
    )
    pricing.set_fetcher(lambda url, country="IN": html)
    out = pricing.price_book("Totally Different Book Name Here")
    assert out["replacement_cost"]["amount"] is None
    assert out["replacement_cost"]["url"] == ""


def test_price_book_empty_query_short_circuits_without_network():
    def boom(url, country="IN"):
        raise AssertionError("network must not be touched for an empty query")

    pricing.set_fetcher(boom)
    out = pricing.price_book("", author="", isbn="")
    assert out["replacement_cost"]["amount"] is None


# --------------------------------------------------------------------------- #
# Item: range from matching results.
# --------------------------------------------------------------------------- #


def test_price_item_success_builds_range():
    captured = {}

    def fake_fetch(url, country="IN"):
        captured["url"] = url
        return ITEM_HTML

    pricing.set_fetcher(fake_fetch)
    out = pricing.price_item("Steel Desk Lamp Light", category="lighting", country="US")

    assert "amazon.com" in captured["url"]
    rc = out["replacement_cost"]
    assert set(rc) == {"low", "high", "source", "url", "retrieved_at"}
    assert rc["low"] == 900.00
    assert rc["high"] == 2300.00
    assert rc["source"] == "amazon"
    assert rc["url"].startswith("https://www.amazon.com/")
    assert rc["retrieved_at"] == date.today().isoformat()


def test_price_item_single_result_low_equals_high():
    html = (
        '<div data-asin="S1"><h2 class="a-text-normal"><span>Blue Ceramic Vase Decor</span></h2>'
        '<span class="a-offscreen">750.00</span></div>'
    )
    pricing.set_fetcher(lambda url, country="IN": html)
    out = pricing.price_item("Blue Ceramic Vase Decor")
    rc = out["replacement_cost"]
    assert rc["low"] == rc["high"] == 750.00
    assert rc["source"] == "amazon"


ABE_HTML = """
<html><body>
<li data-srp-item-role="listing">
  <span data-test-id="listing-price">US$ 9.36</span>
  <span data-test-id="listing-condition">Used - Good</span>
  <a data-test-id="listing-title-link" href="/Pragmatic/123/bd">The Pragmatic Programmer</a>
</li>
<li data-srp-item-role="listing">
  <span data-test-id="listing-price">US$ 40.00</span>
  <span data-test-id="listing-condition">New</span>
  <a data-test-id="listing-title-link" href="/Pragmatic/456/bd">The Pragmatic Programmer</a>
</li>
</body></html>
"""


def test_price_book_used_value_from_abebooks_local():
    # US is a local AbeBooks market → no conversion, picks the cheapest USED offer.
    pricing.set_fetcher(lambda url, country="IN": None)   # no amazon replacement
    pricing.set_abe_fetcher(lambda url: ABE_HTML)
    uv = pricing.price_book("The Pragmatic Programmer", "Andrew Hunt",
                            country="US", currency="USD")["used_value"]
    assert uv["amount"] == 9.36                 # cheapest USED, not the $40 New
    assert uv["source"] == "abebooks"
    assert uv["condition_assumed"] == "Used - Good"
    assert uv["url"].startswith("https://www.abebooks.com/")


def test_price_book_used_value_converted_for_nonlocal_market():
    # IN has no local AbeBooks → price on abebooks.com (USD) and convert to INR, labelled.
    pricing.set_fetcher(lambda url, country="IN": None)
    pricing.set_abe_fetcher(lambda url: ABE_HTML)
    uv = pricing.price_book("The Pragmatic Programmer", country="IN", currency="INR")["used_value"]
    assert uv["amount"] and uv["amount"] > 9.36           # converted up to INR
    assert "converted from USD" in uv["condition_assumed"]


def test_price_book_used_never_exceeds_new():
    # AbeBooks keyword search can drift to a pricier collectible edition. If the "used"
    # price comes out ABOVE the new replacement price, it's a wrong-book match → dropped,
    # never asserted as a used value higher than new.
    pricey = ABE_HTML.replace("US$ 9.36", "US$ 1500.00")  # above the ~899 new price
    pricing.set_fetcher(lambda url, country="IN": BOOK_HTML)   # amazon new price exists
    pricing.set_abe_fetcher(lambda url: pricey)
    out = pricing.price_book("The Pragmatic Programmer", "Andrew Hunt",
                             country="US", currency="USD")
    assert out["replacement_cost"]["amount"] is not None
    assert out["used_value"]["amount"] is None                 # used > new → dropped


def test_price_book_drops_junk_placeholder_and_mismatched_offers():
    # A $1 ship-cost placeholder and an unrelated edition must both be filtered: only the
    # title-matching, above-floor offer survives.
    html = """
    <html><body>
    <li data-srp-item-role="listing">
      <span data-test-id="listing-price">US$ 1.00</span>
      <span data-test-id="listing-condition">Used - Good</span>
      <a data-test-id="listing-title-link" href="/p/1/bd">The Pragmatic Programmer</a>
    </li>
    <li data-srp-item-role="listing">
      <span data-test-id="listing-price">US$ 80.00</span>
      <span data-test-id="listing-condition">Used - Good</span>
      <a data-test-id="listing-title-link" href="/p/2/bd">Modern Interpreters of Something Else</a>
    </li>
    <li data-srp-item-role="listing">
      <span data-test-id="listing-price">US$ 12.00</span>
      <span data-test-id="listing-condition">Used - Good</span>
      <a data-test-id="listing-title-link" href="/p/3/bd">The Pragmatic Programmer</a>
    </li>
    </body></html>
    """
    pricing.set_fetcher(lambda url, country="IN": None)
    pricing.set_abe_fetcher(lambda url: html)
    uv = pricing.price_book("The Pragmatic Programmer", country="US",
                            currency="USD")["used_value"]
    assert uv["amount"] == 12.0    # $1 junk + mismatched $80 both dropped


def test_price_book_cross_market_replacement_conversion():
    # No local (IN) replacement, but amazon.com has one → convert + mark converted=True.
    def fetch(url, country="IN"):
        return BOOK_HTML if "amazon.com/" in url else None
    pricing.set_fetcher(fetch)
    rc = pricing.price_book("The Pragmatic Programmer", "Andrew Hunt",
                            country="IN", currency="INR")["replacement_cost"]
    assert rc["converted"] is True
    assert rc["amount"] and rc["source"] == "amazon.com (converted)"


def test_price_book_flags_signed_as_rare():
    signed = ABE_HTML.replace("Used - Good", "Used - Signed by author")
    pricing.set_fetcher(lambda url, country="IN": None)
    pricing.set_abe_fetcher(lambda url: signed)
    # Title must match the fixture listing ("The Pragmatic Programmer") or the offer is
    # dropped by the title-match filter before the rare signal can be read.
    out = pricing.price_book("The Pragmatic Programmer", "", country="US", currency="USD")
    assert out["rare"] is True


def test_price_item_trims_loose_match_outliers():
    # Three real matches for a "white cube shelving unit" cluster around ~3000, plus
    # two loose matches (a cheap hook, a premium wardrobe) that only share a token.
    # The old min/max-over-all-score>=0.4 logic reported 199..18999; the fix keeps
    # the range tight around the strong matches.
    html = """
    <div data-asin="A"><h2><span>White Cube Shelving Unit Storage</span></h2>
      <span class="a-offscreen">2,999.00</span></div>
    <div data-asin="B"><h2><span>White Cube Shelving Unit 4 Door</span></h2>
      <span class="a-offscreen">3,499.00</span></div>
    <div data-asin="C"><h2><span>White Cube Shelving Unit Bookcase</span></h2>
      <span class="a-offscreen">2,750.00</span></div>
    <div data-asin="D"><h2><span>White Adhesive Wall Hook</span></h2>
      <span class="a-offscreen">199.00</span></div>
    <div data-asin="E"><h2><span>Premium White Wardrobe Unit Large</span></h2>
      <span class="a-offscreen">18,999.00</span></div>
    """
    pricing.set_fetcher(lambda url, country="IN": html)
    rc = pricing.price_item("White Cube Shelving Unit")["replacement_cost"]
    # Range must reflect the shelving cluster, not the hook/wardrobe outliers.
    assert rc["low"] == 2750.00
    assert rc["high"] == 3499.00


def test_price_item_blocked_returns_none_no_fabrication():
    pricing.set_fetcher(lambda url, country="IN": None)
    out = pricing.price_item("Anything At All")
    rc = out["replacement_cost"]
    assert rc["low"] is None
    assert rc["high"] is None
    assert rc["source"] == ""
    assert rc["url"] == ""
    assert rc["retrieved_at"] == date.today().isoformat()


# --------------------------------------------------------------------------- #
# Price parsing unit tests.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1,299.00", 1299.00),
        ("$12.34", 12.34),
        ("₹999", 999.0),
        ("1.299,00", 1299.00),  # European grouping
        ("", None),
        ("free", None),
        ("0.00", None),  # non-positive rejected
    ],
)
def test_parse_price(text, expected):
    assert pricing._parse_price(text) == expected


# --------------------------------------------------------------------------- #
# Domain mapping.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "country,domain",
    [
        ("IN", "amazon.in"),
        ("US", "amazon.com"),
        ("GB", "amazon.co.uk"),
        ("DE", "amazon.de"),
        ("AE", "amazon.ae"),
        ("SA", "amazon.sa"),
        ("MX", "amazon.com.mx"),
        ("TR", "amazon.com.tr"),
        ("BR", "amazon.com.br"),
        ("ZZ", "amazon.com"),  # unknown -> default
    ],
)
def test_domain_mapping(country, domain):
    assert pricing._amazon_domain(country) == domain


def test_default_fetch_never_raises_without_network(monkeypatch):
    """The real fetcher degrades to None rather than raising when offline."""
    # Point the proxy helper at a non-existent repo so no optional deps load.
    monkeypatch.setattr(pricing, "_SCRAPING_REPO", "/nonexistent/path")
    result = pricing._default_fetch("http://127.0.0.1:0/definitely-not-listening", "IN")
    assert result is None


def test_result_shapes_match_schema():
    """The returned dict keys must match backend.schema field names exactly."""
    from backend.schema import ItemReplacementCost, ReplacementCost, UsedValue

    pricing.set_fetcher(lambda url, country="IN": None)
    book = pricing.price_book("x", author="y")
    assert set(book["replacement_cost"]) == set(ReplacementCost.model_fields)
    assert set(book["used_value"]) == set(UsedValue.model_fields)

    item = pricing.price_item("z")
    assert set(item["replacement_cost"]) == set(ItemReplacementCost.model_fields)
