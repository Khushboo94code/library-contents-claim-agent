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
# Public API
# --------------------------------------------------------------------------- #


def price_book(
    title: str,
    author: str = "",
    isbn: str = "",
    country: str = "IN",
    currency: str = "INR",
) -> dict:
    """Source a book's new (replacement) and used price from the local Amazon.

    Returns ``{"replacement_cost": ReplacementCost, "used_value": UsedValue}``.
    Amounts are ``None`` when nothing could be sourced — never fabricated.

    ``currency`` is informational here: we read the local marketplace in its own
    currency, so ``converted`` is always ``False`` (no cross-market conversion).
    """
    retrieved_at = today_iso()
    result = _empty_book_result(retrieved_at)

    query = " ".join(p for p in (title, author) if p).strip()
    if not query and isbn:
        query = isbn
    if not query:
        return result

    # Prefer an ISBN search (exact edition) when we have one, else title+author.
    candidates = _search_amazon(isbn, country, books=True) if isbn else []
    if not candidates:
        candidates = _search_amazon(query, country, books=True)
    if not candidates:
        return result

    best = candidates[0]
    prices = best.get("prices") or []
    if not prices:
        return result

    # Replacement (new) = the current selling price = the lowest price shown on the
    # card (the larger value, if any, is the struck-through MRP, not a used offer).
    result["replacement_cost"].update(
        {"amount": prices[0], "source": "amazon", "url": best["url"]}
    )
    # Used value is NOT inferable from a search card (the second price is the MRP).
    # A real used figure needs the product page's "other sellers" (AOD) — left None
    # here rather than mislabel the list price as used.
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
