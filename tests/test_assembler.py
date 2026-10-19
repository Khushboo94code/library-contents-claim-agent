"""Proves the two load-bearing invariants of the output contract:

* totals are derived from the lines (code, not LLM), and
* an unknown price is a blank that is *excluded* from totals, never a zero.
"""

from backend.assembler import assemble_packet, compute_totals
from backend.schema import (
    Book,
    Item,
    ItemReplacementCost,
    ReplacementCost,
    Room,
    Sweep,
    UsedValue,
)


def _priced_book(bid: str, new: float, used: float) -> Book:
    return Book(
        id=bid,
        status="identified",
        title=f"Book {bid}",
        replacement_cost=ReplacementCost(amount=new, source="amazon", url="http://x", retrieved_at="2026-10-17"),
        used_value=UsedValue(amount=used, source="amazon", url="http://x", retrieved_at="2026-10-17", condition_assumed="good"),
    )


def test_totals_sum_from_lines():
    books = [_priced_book("b1", 100.0, 60.0), _priced_book("b2", 200.0, 120.0)]
    totals = compute_totals(books, [], shelf_run_m=1.25)
    assert totals.book_count == 2
    assert totals.books_identified == 2
    assert totals.books_replacement_cost == 300.0
    assert totals.books_used_value == 180.0
    assert totals.shelf_run_m == 1.25
    assert totals.excluded_from_totals == 0


def test_unknown_price_is_excluded_not_zero():
    # An identified book with no price must not drag the total down with a 0.
    unpriced = Book(id="b3", status="identified", title="No price found")
    books = [_priced_book("b1", 100.0, 60.0), unpriced]
    totals = compute_totals(books, [], shelf_run_m=0.0)
    assert totals.books_replacement_cost == 100.0  # unpriced excluded, not summed as 0
    assert totals.excluded_from_totals == 1


def test_unidentified_book_counts_but_is_flagged():
    books = [Book(id="u1", status="unidentified", spine_height_cm=21.0, spine_thickness_cm=2.4)]
    packet = assemble_packet(Sweep(country="IN", currency="INR"), Room(), books, [], shelf_run_m=0.024)
    assert packet.totals.book_count == 1
    assert packet.totals.books_unidentified == 1
    assert any(r.ref_id == "u1" for r in packet.review_queue)


def test_item_range_totals_and_appraisal_flag():
    items = [
        Item(id="i1", category="lamp", status="range", replacement_cost=ItemReplacementCost(low=40.0, high=90.0)),
        Item(id="i2", category="portrait", status="needs_appraisal"),
    ]
    packet = assemble_packet(Sweep(), Room(), [], items, shelf_run_m=0.0)
    assert packet.totals.items_replacement_cost_low == 40.0
    assert packet.totals.items_replacement_cost_high == 90.0
    assert packet.totals.excluded_from_totals == 1  # the appraisal item has no price
    assert any(r.ref_id == "i2" and "appraisal" in r.reason for r in packet.review_queue)


def test_packet_validates_against_contract():
    packet = assemble_packet(
        Sweep(id="s1", country="IN", currency="INR"),
        Room(floor_area_m2=12.3, scale_method="arkit+fiducial", confidence=0.8),
        [_priced_book("b1", 100.0, 60.0)],
        [],
        shelf_run_m=0.03,
    )
    dumped = packet.model_dump()
    assert {"sweep", "room", "books", "items", "totals", "review_queue"} <= set(dumped)
    assert "evidence" in dumped and "agent_observations" in dumped
    assert dumped["totals"]["books_replacement_cost"] == 100.0
