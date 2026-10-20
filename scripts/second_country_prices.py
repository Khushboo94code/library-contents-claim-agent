#!/usr/bin/env python3
"""Second-country pricing proof (brief requirement 3): price the same 10 identified books
in TWO countries side-by-side, to show locale is a *setting*, not hardcoded.

Reads a finished claim packet, takes its first 10 identified books, and re-prices each in
the packet's own country and in a second country, emitting a markdown + JSON comparison.

Usage:
    .venv/bin/python scripts/second_country_prices.py <sweep_id | packet.json path> [second_country]

Example:
    .venv/bin/python scripts/second_country_prices.py sweep-1792430605 US
"""
from __future__ import annotations

import json
import pathlib
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from backend.pricing.price import AMAZON_DOMAINS, price_book  # noqa: E402

CUR = {"IN": "INR", "US": "USD", "GB": "GBP", "DE": "EUR", "AE": "AED",
       "SA": "SAR", "MX": "MXN", "TR": "TRY", "BR": "BRL"}
ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_packet(arg: str) -> dict:
    p = pathlib.Path(arg)
    if not p.exists():
        p = ROOT / "data" / "sweeps" / arg / "packet.json"
    if not p.exists():
        sys.exit(f"packet not found: {arg}")
    return json.loads(p.read_text())


def _fmt(pr: dict) -> str:
    rc = pr.get("replacement_cost", {})
    uv = pr.get("used_value", {})
    rep = f"{rc['amount']:.0f}" if rc.get("amount") else "—"
    if rc.get("converted"):
        rep += " (conv)"
    used = f"{uv['amount']:.0f}" if uv.get("amount") else "—"
    return rep, used, (rc.get("url") or uv.get("url") or "")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    packet = _load_packet(sys.argv[1])
    country_a = (packet.get("sweep", {}).get("country") or "IN").upper()
    country_b = (sys.argv[2].upper() if len(sys.argv) > 2 else ("US" if country_a != "US" else "GB"))
    cur_a, cur_b = CUR.get(country_a, country_a), CUR.get(country_b, country_b)

    books = [b for b in packet.get("books", []) if b.get("status") == "identified"
             and b.get("title")][:10]
    if not books:
        sys.exit("no identified books in packet to price")

    print(f"Pricing {len(books)} books in {country_a}/{cur_a} vs {country_b}/{cur_b} "
          f"(domains: {AMAZON_DOMAINS.get(country_a)} / {AMAZON_DOMAINS.get(country_b)})\n")

    rows, out = [], []
    for b in books:
        t, a, isbn = b["title"], b.get("author", ""), b.get("isbn", "")
        pa = price_book(t, a, isbn, country_a, cur_a)
        pb = price_book(t, a, isbn, country_b, cur_b)
        ra, ua, ua_url = _fmt(pa)
        rb, ub, ub_url = _fmt(pb)
        rows.append(f"| {t[:34]:<34} | {cur_a} {ra:>9} | {cur_a} {ua:>7} | {cur_b} {rb:>9} | {cur_b} {ub:>7} |")
        out.append({"title": t, country_a: pa, country_b: pb})
        print(f"  {t[:40]:<40} {country_a}:{ra:>8}/{ua:<6}  {country_b}:{rb:>8}/{ub}")

    header = (f"# Second-country pricing — {country_a} vs {country_b}\n\n"
              f"Same {len(books)} books, priced from each marketplace. Proves locale is a setting.\n\n"
              f"| Title | {country_a} replacement | {country_a} used | {country_b} replacement | {country_b} used |\n"
              f"|---|---|---|---|---|\n" + "\n".join(rows) + "\n")
    md_path = ROOT / "ground_truth" / f"second_country_{country_a}_vs_{country_b}.md"
    json_path = ROOT / "ground_truth" / f"second_country_{country_a}_vs_{country_b}.json"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(header)
    json_path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {md_path}\nwrote {json_path}")


if __name__ == "__main__":
    main()
