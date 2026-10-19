"""Assembles the final claim packet and computes all totals **in code**.

The brief is explicit: "Totals are computed in code from the lines, never
written by a language model." Every function here operates purely on the
structured line items — no LLM is involved. The same rule drives the review
queue: any line the system is not confident in is routed here with a reason.
"""

from __future__ import annotations

from backend.schema import (
    AgentObservation,
    Book,
    ClaimPacket,
    EvidenceItem,
    Item,
    ReviewItem,
    Room,
    Sweep,
    Totals,
)

# Lines below these confidence thresholds are routed to the review queue.
BOOK_ID_CONFIDENCE_FLOOR = 0.6
ITEM_CONFIDENCE_FLOOR = 0.6


def _has_price(amount: float | None) -> bool:
    """A price counts toward totals only when it is a real, sourced number."""
    return amount is not None and amount > 0


def compute_totals(books: list[Book], items: list[Item], shelf_run_m: float) -> Totals:
    """Derive every total from the line items. Never called with LLM output.

    A book whose replacement cost is unknown is counted in
    ``excluded_from_totals`` and contributes nothing to the money sums — an
    unknown price is a blank, not a zero.
    """
    totals = Totals(shelf_run_m=round(shelf_run_m, 3))

    totals.book_count = len(books)
    totals.books_identified = sum(1 for b in books if b.status == "identified")
    totals.books_unidentified = sum(1 for b in books if b.status == "unidentified")

    excluded = 0
    for b in books:
        if _has_price(b.replacement_cost.amount):
            totals.books_replacement_cost += b.replacement_cost.amount  # type: ignore[operator]
        else:
            excluded += 1
        if _has_price(b.used_value.amount):
            totals.books_used_value += b.used_value.amount  # type: ignore[operator]

    for it in items:
        if _has_price(it.replacement_cost.low):
            totals.items_replacement_cost_low += it.replacement_cost.low  # type: ignore[operator]
        if _has_price(it.replacement_cost.high):
            totals.items_replacement_cost_high += it.replacement_cost.high  # type: ignore[operator]
        if not _has_price(it.replacement_cost.low) and not _has_price(
            it.replacement_cost.high
        ):
            excluded += 1

    totals.excluded_from_totals = excluded

    # Round money to 2 dp without letting float noise leak into the packet.
    totals.books_replacement_cost = round(totals.books_replacement_cost, 2)
    totals.books_used_value = round(totals.books_used_value, 2)
    totals.items_replacement_cost_low = round(totals.items_replacement_cost_low, 2)
    totals.items_replacement_cost_high = round(totals.items_replacement_cost_high, 2)
    return totals


def build_review_queue(books: list[Book], items: list[Item], room: Room) -> list[ReviewItem]:
    """Every low-confidence or unpriced line, with the reason, for an adjuster."""
    queue: list[ReviewItem] = []

    for b in books:
        if b.status == "needs_appraisal":
            queue.append(ReviewItem(ref_id=b.id, reason="flagged for human appraisal (rare/signed/high-value)"))
        elif b.status == "unidentified":
            queue.append(ReviewItem(ref_id=b.id, reason="spine not legible — logged with dimensions, no title guessed"))
        elif b.id_confidence is not None and b.id_confidence < BOOK_ID_CONFIDENCE_FLOOR:
            queue.append(ReviewItem(ref_id=b.id, reason=f"low identification confidence ({b.id_confidence:.2f})"))
        if b.status == "identified" and not _has_price(b.replacement_cost.amount):
            queue.append(ReviewItem(ref_id=b.id, reason="identified but no local price found — excluded from totals"))

    for it in items:
        if it.status == "needs_appraisal":
            queue.append(ReviewItem(ref_id=it.id, reason="art/high-value item — needs appraisal"))
        elif it.confidence is not None and it.confidence < ITEM_CONFIDENCE_FLOOR:
            queue.append(ReviewItem(ref_id=it.id, reason=f"low classification confidence ({it.confidence:.2f})"))

    if room.confidence is not None and room.confidence < 0.6:
        queue.append(ReviewItem(ref_id="room", reason=f"low measurement confidence ({room.confidence:.2f})"))

    return queue


def build_evidence(books: list[Book], items: list[Item]) -> list[EvidenceItem]:
    """The evidence manifest: every priced/identified line linked to its saved frame,
    so an adjuster can trace any figure back to an image (inherited from the reference)."""
    manifest: list[EvidenceItem] = []
    for b in books:
        if b.frame_ref:
            manifest.append(EvidenceItem(ref_id=b.id, frame_ref=b.frame_ref, kind="book",
                                         label=b.title or "unidentified spine"))
    for it in items:
        if it.frame_ref:
            manifest.append(EvidenceItem(ref_id=it.id, frame_ref=it.frame_ref, kind="item",
                                         label=it.description or it.category))
    return manifest


def assemble_packet(
    sweep: Sweep,
    room: Room,
    books: list[Book],
    items: list[Item],
    shelf_run_m: float,
    agent_observations: list[AgentObservation] | None = None,
) -> ClaimPacket:
    """Compose the full packet with code-computed totals, review queue, and evidence manifest."""
    totals = compute_totals(books, items, shelf_run_m)
    review_queue = build_review_queue(books, items, room)
    return ClaimPacket(
        sweep=sweep,
        room=room,
        books=books,
        items=items,
        totals=totals,
        review_queue=review_queue,
        evidence=build_evidence(books, items),
        agent_observations=agent_observations or [],
    )
