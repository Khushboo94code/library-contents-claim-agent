"""Real, sourced local-market pricing for books and household items.

Contract (brief, rule 1): a price is either *fetched from a real page* or it is
``None``. This module never fabricates an amount, a source, or a URL. Every
non-``None`` amount carries the Amazon product URL it was scraped from, so a
claims adjuster can open the page and verify it.

Returned shapes mirror :mod:`backend.schema` exactly:

* :func:`price_book` -> ``{"replacement_cost": {...ReplacementCost...},
  "used_value": {...UsedValue...}}``
* :func:`price_item` -> ``{"replacement_cost": {...ItemReplacementCost...}}``

Design for testability and no-network safety:

* The one place that touches the network is :data:`_FETCHER`, a module-level
  callable ``(url, country) -> html_str | None``. Tests inject a fake fetcher;
  the real one (:func:`_default_fetch`) is pure best-effort and returns ``None``
  on any failure (blocked, timeout, missing dependency) rather than raising.
* Heavy/optional dependencies (``curl_cffi``, the scraping repo's proxy manager)
  are imported lazily inside the fetcher. The module imports cleanly with no
  network and no optional deps installed.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import time
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib.parse import quote_plus, urljoin

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# country (ISO-3166 alpha-2) -> Amazon marketplace host.
AMAZON_DOMAINS = {
    "IN": "amazon.in",
    "US": "amazon.com",
    "GB": "amazon.co.uk",
    "DE": "amazon.de",
    "AE": "amazon.ae",
    "SA": "amazon.sa",
    "MX": "amazon.com.mx",
    "TR": "amazon.com.tr",
    "BR": "amazon.com.br",
}

_DEFAULT_DOMAIN = "amazon.com"

# curl_cffi browser profile to impersonate, matching the scraping repo's usage.
_IMPERSONATE = os.environ.get("AMAZON_IMPERSONATE", "chrome110")
_TIMEOUT = int(os.environ.get("AMAZON_TIMEOUT", "45"))  # seconds, per request
# Amazon 503s a single request even via proxy; each retry rotates the exit IP.
_MAX_RETRIES = int(os.environ.get("AMAZON_MAX_RETRIES", "6"))

# Path to the reusable scraping framework (its .env holds the working proxy).
_SCRAPING_REPO = os.environ.get("GC_SCRAPING_REPO", "/Users/muskan/Desktop/scraping")
_PROXY_CACHE: dict[str, Optional[str]] = {}


def _repo_env_proxy() -> Optional[str]:
    """Read PROXY_URL from the scraping repo's .env (the proven Decodo gateway)."""
    try:
        envp = pathlib.Path(_SCRAPING_REPO) / ".env"
        if envp.exists():
            for line in envp.read_text().splitlines():
                m = re.match(r"\s*PROXY_URL\s*=\s*['\"]?([^'\"\n]+)", line)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass
    return None


def _looks_complete(html: str) -> bool:
    """A usable Amazon results page has products AND prices. The India IP sometimes
    returns a degraded page (a few sponsored cards, no prices) — reject it and retry."""
    return bool(html) and "data-asin" in html and html.count("a-offscreen") >= 3


def today_iso() -> str:
    """ISO-8601 date used for ``retrieved_at``."""
    return date.today().isoformat()


# --------------------------------------------------------------------------- #
# Empty (unknown) results — the only thing returned when we cannot source a price.
# --------------------------------------------------------------------------- #


def _empty_book_result(retrieved_at: str) -> dict:
    """Unknown book price: amounts ``None``, no source, no url. Never fabricated."""
    return {
        "replacement_cost": {
            "amount": None,
            "source": "",
            "url": "",
            "retrieved_at": retrieved_at,
            "converted": False,
        },
        "used_value": {
            "amount": None,
            "source": "",
            "url": "",
            "retrieved_at": retrieved_at,
            "condition_assumed": "",
        },
    }


def _empty_item_result(retrieved_at: str) -> dict:
    return {
        "replacement_cost": {
            "low": None,
            "high": None,
            "source": "",
            "url": "",
            "retrieved_at": retrieved_at,
        }
    }


# --------------------------------------------------------------------------- #
# Network layer — injectable so tests never hit the network.
# --------------------------------------------------------------------------- #


def _proxy_for(country: str) -> Optional[str]:
    """Resolve a working proxy URL (Amazon blocks direct connections).

    Order: explicit env (country-specific, then generic) -> the scraping repo's .env
    PROXY_URL (proven Decodo gateway) -> proxy_manager routing (needs DB/vault).
    Cached per country.
    """
    cc = (country or "").upper()
    if cc in _PROXY_CACHE:
        return _PROXY_CACHE[cc]

    proxy = os.environ.get(f"PROXY_URL_{cc}") or os.environ.get("PROXY_URL") or _repo_env_proxy()
    if not proxy:
        try:  # last resort: the repo's routing manager (needs its config backends)
            if _SCRAPING_REPO and _SCRAPING_REPO not in sys.path:
                sys.path.insert(0, _SCRAPING_REPO)
            from common.proxy import Country, proxy_manager  # type: ignore

            code = (country or "").lower()
            resolved = proxy_manager.resolve(job="lidar_pricing_amazon",
                                             country=Country.__members__.get(cc) or code or None)
            proxy = getattr(resolved, "url", None)
        except Exception:
            proxy = None
    _PROXY_CACHE[cc] = proxy
    return proxy


def _default_fetch(url: str, country: str = "IN") -> Optional[str]:
    """Fetch ``url`` as HTML via the proxy, retrying on Amazon's 503 (each retry
    rotates the exit IP). Returns the HTML only once it looks like a real results
    page (contains ``data-asin``). Never raises."""
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-IN,en;q=0.9",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        ),
    }
    try:
        import curl_cffi.requests as cffi  # type: ignore
    except Exception:
        return None

    # Option A: route through the deployed GCP Cloud Function (which proxies via Decodo
    # and retries server-side). Set CLOUD_FETCH_URL to enable.
    cf = os.environ.get("CLOUD_FETCH_URL")
    if cf:
        secret = os.environ.get("CLOUD_FETCH_SECRET", "")
        # The function already retries server-side and usually returns in a few seconds.
        # Keep the client budget small (2 tries x short timeout) so an unpriceable book
        # doesn't stall the whole pipeline — each call is a fresh India container/IP.
        for _ in range(int(os.environ.get("CLOUD_FETCH_RETRIES", "4"))):
            try:
                r = cffi.get(cf, params={"url": url}, headers={"X-Fetch-Secret": secret},
                             timeout=int(os.environ.get("CLOUD_FETCH_TIMEOUT", "40")))
                if r.status_code == 200 and _looks_complete(r.text):
                    return r.text
            except Exception:
                pass
            time.sleep(0.4)
        return None

    # Option B: local curl_cffi + residential/DC proxy + retries (each retry rotates IP).
    proxy = _proxy_for(country)
    proxies = {"http": proxy, "https": proxy} if proxy else None
    for _ in range(_MAX_RETRIES):
        try:
            resp = cffi.get(url, impersonate=_IMPERSONATE, proxies=proxies,
                            headers=headers, timeout=_TIMEOUT, verify=False)
            if resp is not None and resp.status_code == 200 and _looks_complete(resp.text):
                return resp.text
        except Exception:
            pass
        time.sleep(0.6)
    return None


# The injectable fetch seam. Tests replace this; signature: (url, country) -> html|None.
_FETCHER: Callable[..., Optional[str]] = _default_fetch


def set_fetcher(fetcher: Callable[..., Optional[str]]) -> None:
    """Override the network layer (used by tests). ``fetcher(url, country) -> html|None``."""
    global _FETCHER
    _FETCHER = fetcher


# --------------------------------------------------------------------------- #
# Amazon search-results parsing (stdlib only, no bs4/lxml dependency).
# --------------------------------------------------------------------------- #


class _SearchResultParser(HTMLParser):
    """Pull (asin, title, price) tuples out of an Amazon search-results page.

    Amazon renders each organic result as ``<div data-asin="..." ...>`` with the
    title inside an ``<h2>``/``<span>`` and the price in
    ``<span class="a-offscreen">$12.34</span>``. We track the current result
    div by ASIN and attach text/prices to it. Tolerant of markup drift: anything
    we fail to associate is simply dropped (yields fewer results, never a crash).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._depth = 0
        self._cur: Optional[dict] = None
        self._cur_start_depth = 0
        self._capture_title = False
        self._capture_price = False

    def handle_starttag(self, tag, attrs):
        self._depth += 1
        a = dict(attrs)

        # Start of a new organic result card.
        if tag == "div" and a.get("data-asin"):
            asin = a.get("data-asin", "").strip()
            # Amazon emits empty-asin layout divs; only real ASINs are products.
            if asin:
                self._cur = {"asin": asin, "title": "", "prices": []}
                self._cur_start_depth = self._depth
                self.results.append(self._cur)

        if self._cur is None:
            return

        cls = a.get("class", "")
        # Title text usually lives under an <h2> or an aria-label link/span.
        if tag in ("h2", "span") and ("a-text-normal" in cls or tag == "h2"):
            self._capture_title = True
        # Price text.
        if tag == "span" and "a-offscreen" in cls:
            self._capture_price = True

    def handle_endtag(self, tag):
        if self._cur is not None and self._depth <= self._cur_start_depth:
            self._cur = None
        self._capture_title = False
        self._capture_price = False
        self._depth -= 1

    def handle_data(self, data):
        if self._cur is None:
            return
        text = data.strip()
        if not text:
            return
        if self._capture_title and not self._cur["title"]:
            self._cur["title"] = unescape(text)
        if self._capture_price:
            amt = _parse_price(text)
            if amt is not None:
                self._cur["prices"].append(amt)


_PRICE_RE = re.compile(r"[\d][\d.,\s]*")


def _parse_price(text: str) -> Optional[float]:
    """Extract a numeric amount from a price string like ``₹1,299.00`` / ``$12.34``.

    Returns ``None`` if no plausible number is present. Handles both
    ``1,299.00`` (comma thousands) and ``1.299,00`` (European) conservatively by
    treating the last separator as the decimal point only when it leaves 2 digits.
    """
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    raw = m.group(0).strip().replace(" ", "")
    if not raw:
        return None
    # Decide decimal separator: whichever of '.' or ',' appears last, if it
    # yields a 2-digit fraction, is the decimal point; everything else is noise.
    last_dot = raw.rfind(".")
    last_comma = raw.rfind(",")
    dec_pos = max(last_dot, last_comma)
    if dec_pos != -1 and len(raw) - dec_pos - 1 == 2:
        integer = re.sub(r"[.,]", "", raw[:dec_pos])
        frac = raw[dec_pos + 1 :]
        candidate = f"{integer}.{frac}"
    else:
        candidate = re.sub(r"[.,]", "", raw)
    try:
        value = float(candidate)
    except ValueError:
        return None
    return value if value > 0 else None


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 1}


def _match_score(query: str, candidate_title: str) -> float:
    """Fraction of query tokens present in the candidate title (0..1)."""
    q = _tokens(query)
    if not q:
        return 0.0
    c = _tokens(candidate_title)
    return len(q & c) / len(q)


def _amazon_domain(country: str) -> str:
    return AMAZON_DOMAINS.get((country or "").upper(), _DEFAULT_DOMAIN)


def _search_url(domain: str, query: str, books: bool) -> str:
    base = f"https://www.{domain}/s?k={quote_plus(query)}"
    if books:
        base += "&i=stripbooks"
    return base


def _product_url(domain: str, asin: str) -> str:
    return urljoin(f"https://www.{domain}/", f"dp/{asin}")


def _search_amazon(query: str, country: str, books: bool) -> list[dict]:
    """Return matched product dicts (asin, title, prices, url) best-match first,
    parsed with BeautifulSoup from the real Amazon results page. Empty on any
    failure/no match (callers turn that into ``None`` amounts). Never raises."""
    try:
        domain = _amazon_domain(country)
        html = _FETCHER(_search_url(domain, query, books), country)
        if not html:
            return []
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return []
        candidates = []
        for card in soup.select("div[data-asin]"):
            asin = (card.get("data-asin") or "").strip()
            if not asin:
                continue
            # Title: the whole <h2> text is the most layout-robust; fall back to
            # the known title spans for older/variant cards.
            h2 = card.find("h2")
            title = h2.get_text(" ", strip=True) if h2 else ""
            if not title:
                t = (card.select_one("span.a-size-medium.a-color-base.a-text-normal")
                     or card.select_one("span.a-text-normal")
                     or card.select_one("[data-cy='title-recipe'] a span"))
                title = t.get_text(" ", strip=True) if t else ""
            if not title:
                continue
            # All visible prices on the card (current price + any strikethrough MRP).
            prices = sorted({p for pe in card.select("span.a-offscreen")
                             if (p := _parse_price(pe.get_text())) is not None})
            if not prices:
                continue
            score = _match_score(query, title)
            if score <= 0:
                continue
            candidates.append({"asin": asin, "title": title, "prices": prices,
                               "url": _product_url(domain, asin), "score": score})
        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# AbeBooks — used-book marketplace (new/used offers with condition, per country)
# --------------------------------------------------------------------------- #

# Localized AbeBooks sites (country -> (domain, market currency)). Countries not listed
# have no local AbeBooks, so we price used on abebooks.com and mark the result converted.
ABEBOOKS_LOCAL = {
    "US": ("abebooks.com", "USD"),
    "GB": ("abebooks.co.uk", "GBP"),
    "DE": ("abebooks.de", "EUR"),
    "FR": ("abebooks.fr", "EUR"),
    "IT": ("abebooks.it", "EUR"),
}
_ABE_DEFAULT = ("abebooks.com", "USD")


def _abe_source(country: str) -> tuple[str, str, bool]:
    """(domain, market_currency, is_local) for the used-book search."""
    cc = (country or "").upper()
    if cc in ABEBOOKS_LOCAL:
        dom, cur = ABEBOOKS_LOCAL[cc]
        return dom, cur, True
    dom, cur = _ABE_DEFAULT
    return dom, cur, False


def _default_abe_fetch(url: str) -> Optional[str]:
    """Fetch an AbeBooks search page (direct curl_cffi — AbeBooks serves fine without the
    Amazon proxy). Returns HTML only once it looks like real results. Never raises."""
    try:
        import curl_cffi.requests as cffi  # type: ignore
    except Exception:
        return None
    headers = {"accept-language": "en-US,en;q=0.9", "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")}
    for _ in range(int(os.environ.get("ABE_RETRIES", "3"))):
        try:
            r = cffi.get(url, impersonate=_IMPERSONATE, headers=headers, timeout=_TIMEOUT, verify=False)
            if r is not None and r.status_code == 200 and (
                    "listing-price" in r.text or "srp-item-role" in r.text):
                return r.text
        except Exception:
            pass
        time.sleep(0.5)
    return None


# Injectable AbeBooks fetch seam (tests stub this). Signature: (url) -> html|None.
_ABE_FETCHER: Callable[..., Optional[str]] = _default_abe_fetch


def set_abe_fetcher(fetcher: Callable[..., Optional[str]]) -> None:
    """Override the AbeBooks network layer (tests). ``fetcher(url) -> html|None``."""
    global _ABE_FETCHER
    _ABE_FETCHER = fetcher


def _abe_search_url(domain: str, title: str, author: str, isbn: str) -> str:
    base = f"https://www.{domain}/servlet/SearchResults"
    if isbn:
        return f"{base}?isbn={quote_plus(isbn)}&sortby=17&bsi=0"
    q = " ".join(p for p in (title, author) if p).strip()
    return f"{base}?kn={quote_plus(q)}&sortby=17&bsi=0"  # sortby=17 = price low->high


# A kept AbeBooks offer must share at least this fraction of the book's title tokens —
# otherwise the keyword search drifts to unrelated academic/collectible editions that are
# far pricier than the real book (e.g. "The Bhagavadgita" -> "Modern Indian Interpreters
# of the Bhagavadgita"). And offers below the price floor are $1 placeholder/ship-cost
# junk listings, not a real second-hand value.
_ABE_TITLE_MATCH = float(os.environ.get("ABE_TITLE_MATCH", "0.5"))
_ABE_MIN_PRICE = float(os.environ.get("ABE_MIN_PRICE", "3.0"))  # in the market currency


def _search_abebooks(title: str, author: str, isbn: str, country: str) -> tuple[list[dict], str, bool]:
    """Return (offers, market_currency, is_local). Each offer:
    {amount, condition, url, title}. Offers whose listing title doesn't match the book, or
    that are priced below the junk floor, are dropped. Empty on any failure."""
    domain, market_cur, is_local = _abe_source(country)
    try:
        html = _ABE_FETCHER(_abe_search_url(domain, title, author, isbn))
        if not html:
            return [], market_cur, is_local
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        offers: list[dict] = []
        for el in soup.select('[data-srp-item-role="listing"]'):
            pe = el.select_one('[data-test-id="listing-price"]')
            amt = _parse_price(pe.get_text(strip=True)) if pe else None
            if amt is None or amt < _ABE_MIN_PRICE:   # drop $1 placeholder/ship-cost junk
                continue
            ce = el.select_one('[data-test-id="listing-condition"]')
            ae = el.select_one('[data-test-id="listing-title-link"]')
            otitle = ae.get_text(" ", strip=True) if ae else ""
            # Keep only offers for THIS book (skip when searching by ISBN — already exact).
            if not isbn and title and _match_score(title, otitle) < _ABE_TITLE_MATCH:
                continue
            href = ae.get("href") if ae else ""
            url = href if href.startswith("http") else f"https://www.{domain}{href}"
            offers.append({"amount": amt, "url": url,
                           "condition": ce.get_text(" ", strip=True) if ce else "",
                           "title": otitle})
        return offers, market_cur, is_local
    except Exception:
        return [], market_cur, is_local


# --------------------------------------------------------------------------- #
# FX conversion (for cross-market pricing; label `converted`)
# --------------------------------------------------------------------------- #

# Approximate USD-based fallback rates (1 USD -> X). Used offline / when the live
# endpoint is unavailable. Refreshed rarely; the retrieval is recorded as converted.
_FX_STATIC = {"USD": 1.0, "INR": 83.0, "GBP": 0.79, "EUR": 0.92, "AED": 3.67,
              "SAR": 3.75, "MXN": 17.0, "TRY": 32.0, "BRL": 5.0, "CAD": 1.36}
_FX_CACHE: dict[str, dict] = {}


def _fx_rate(frm: str, to: str) -> Optional[float]:
    """Rate to multiply a `frm` amount by to get `to`. Live (no-key) when FX_LIVE is set,
    else a static table. None if unknown."""
    frm, to = (frm or "").upper(), (to or "").upper()
    if not frm or not to:
        return None
    if frm == to:
        return 1.0
    if os.environ.get("FX_LIVE"):
        try:
            if frm not in _FX_CACHE:
                import curl_cffi.requests as cffi  # type: ignore
                r = cffi.get(f"https://open.er-api.com/v6/latest/{frm}", timeout=15)
                if r.status_code == 200 and r.json().get("result") == "success":
                    _FX_CACHE[frm] = r.json()["rates"]
            if frm in _FX_CACHE and to in _FX_CACHE[frm]:
                return float(_FX_CACHE[frm][to])
        except Exception:
            pass
    if frm in _FX_STATIC and to in _FX_STATIC:
        return _FX_STATIC[to] / _FX_STATIC[frm]
    return None


def _fx_convert(amount: float, frm: str, to: str) -> Optional[float]:
    """Convert `amount` from `frm` to `to`, rounded. None if no rate."""
    rate = _fx_rate(frm, to)
    return round(amount * rate, 2) if rate else None


# Strong signals only — "first edition" alone is far too common to flag. We scan the
# title/author and the CHOSEN offer, not every fuzzy search hit (which caused false positives).
_RARE_SIGNALS = ("signed", "autographed", "antiquarian", "rare book", "limited edition",
                 "collector", "first edition signed")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def price_book(
    title: str,
    author: str = "",
    isbn: str = "",
    country: str = "IN",
    currency: str = "INR",
) -> dict:
    """Source a book's new (replacement) and used price.

    * Replacement (new): the local Amazon marketplace. If no local price is found, we
      price on amazon.com and FX-convert, marking ``converted=True``.
    * Used value: AbeBooks (the used/second-hand book marketplace), localized per country
      where one exists, else abebooks.com converted to the local currency.

    Returns ``{"replacement_cost", "used_value", "rare"}``. Amounts are ``None`` when
    nothing could be sourced — never fabricated. ``rare`` flags likely signed/first-edition/
    antiquarian copies for human appraisal.
    """
    retrieved_at = today_iso()
    result = _empty_book_result(retrieved_at)
    result["rare"] = False

    query = " ".join(p for p in (title, author) if p).strip()
    if not query and isbn:
        query = isbn
    if not query:
        return result

    # --- Replacement (new) from the local Amazon marketplace -------------------------
    candidates = _search_amazon(isbn, country, books=True) if isbn else []
    if not candidates:
        candidates = _search_amazon(query, country, books=True)
    best = candidates[0] if candidates else None
    prices = (best.get("prices") if best else None) or []
    if prices:
        result["replacement_cost"].update(
            {"amount": prices[0], "source": "amazon", "url": best["url"]})
    elif (country or "").upper() != "US":
        # No local price → price on amazon.com and convert (brief: label as converted).
        ref = _search_amazon(query, "US", books=True)
        ref_prices = (ref[0].get("prices") if ref else None) or []
        if ref_prices:
            conv = _fx_convert(ref_prices[0], "USD", currency)
            if conv is not None:
                result["replacement_cost"].update(
                    {"amount": conv, "source": "amazon.com (converted)",
                     "url": ref[0]["url"], "converted": True})

    # --- Used value from AbeBooks -----------------------------------------------------
    offers, market_cur, is_local = _search_abebooks(title, author, isbn, country)
    used = [o for o in offers if "used" in (o.get("condition") or "").lower()]
    pick = (min(used, key=lambda o: o["amount"]) if used
            else (min(offers, key=lambda o: o["amount"]) if offers else None))
    if pick:
        amount = pick["amount"]
        condition = pick.get("condition") or "Used"
        if market_cur != currency:
            conv = _fx_convert(amount, market_cur, currency)
            if conv is not None:
                amount = conv
                condition = f"{condition}; converted from {market_cur}"
        # Safety net: a used copy should not cost MORE than a new one. If it does, the
        # AbeBooks listing is a different/collectible edition that slipped past the title
        # match — drop the used value rather than assert an implausible figure.
        new_amount = result["replacement_cost"].get("amount")
        if new_amount and amount > new_amount:
            pick = None
        else:
            result["used_value"].update(
                {"amount": amount, "source": "abebooks", "url": pick["url"],
                 "condition_assumed": condition})

    # --- Rare / signed / antiquarian flag --------------------------------------------
    # Scan only the book itself and the chosen offer — not every fuzzy search hit.
    blob = " ".join([title, author,
                     (pick or {}).get("condition", ""), (pick or {}).get("title", "")]).lower()
    if any(sig in blob for sig in _RARE_SIGNALS):
        result["rare"] = True

    return result


def price_item(
    description: str,
    category: str = "",
    country: str = "IN",
    currency: str = "INR",
) -> dict:
    """Source a replacement-cost *range* for a household item from local Amazon.

    Returns ``{"replacement_cost": ItemReplacementCost}`` with ``low``/``high``
    spanning the matching results. ``low == high`` when only one price is found;
    both ``None`` when nothing could be sourced — never fabricated.
    """
    retrieved_at = today_iso()
    result = _empty_item_result(retrieved_at)

    query = " ".join(p for p in (description, category) if p).strip()
    if not query:
        return result

    candidates = _search_amazon(query, country, books=False)
    if not candidates:
        return result

    # Build a market range only from products that actually match the item, then
    # trim outliers around the median. Loose matching (the old score>=0.4 over ALL
    # results) produced meaningless ranges — e.g. a "white cube shelving unit"
    # spanning a 320 accessory to a 19,611 wardrobe. Two guards fix that:
    #   1. relevance: keep only strong matches (near the best score, and >= 0.5),
    #      capped to the top few so a long tail of weak listings can't widen it;
    #   2. robustness: drop prices far from the median (unrelated cheap/premium
    #      SKUs that slipped through), so low/high reflect the real product.
    best_score = candidates[0].get("score", 0.0)
    cutoff = max(0.5, best_score - 0.15)
    priced = [(c["prices"][0], c["url"])
              for c in candidates
              if c.get("prices") and c.get("score", 0.0) >= cutoff]

    # Fall back to the single best match if the stricter filter found nothing.
    if not priced and candidates and candidates[0].get("prices"):
        priced = [(candidates[0]["prices"][0], candidates[0]["url"])]

    if not priced:
        return result

    priced = priced[:6]  # top matches are already best-score-first
    prices = [p for p, _ in priced]
    med = sorted(prices)[len(prices) // 2]
    # Keep prices within a sane band of the median (0.4x .. 2.5x); this discards
    # an odd ultra-cheap part or premium bundle that survived relevance scoring.
    kept = [(p, u) for p, u in priced if 0.4 * med <= p <= 2.5 * med] or priced
    kept_prices = [p for p, _ in kept]
    url = next((u for _, u in kept), kept[0][1])

    result["replacement_cost"].update(
        {
            "low": min(kept_prices),
            "high": max(kept_prices),
            "source": "amazon",
            "url": url,
        }
    )
    return result
