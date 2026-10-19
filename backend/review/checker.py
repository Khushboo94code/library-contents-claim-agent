"""Independent checker agent — audits a finished packet and reports disagreements.

Inherited from the reference app's deterministic fraud/safety gate, and the brief's stretch
goal ("a second pass by an independent checker agent that audits the packet"). This runs AFTER
assembly and is deterministic (no LLM), so it can police the exact integrity rules the brief
cares about — most importantly, a price with no retrievable source is a fabricated price.
Findings are appended to the review queue prefixed with "checker:".
"""

from __future__ import annotations

from backend.assembler import compute_totals
from backend.schema import ClaimPacket, ReviewItem


def audit_packet(packet: ClaimPacket) -> list[ReviewItem]:
    out: list[ReviewItem] = []

    # 1. Totals must equal a fresh code recomputation (catch any drift / tampering).
    recomputed = compute_totals(packet.books, packet.items, packet.totals.shelf_run_m)
    if abs(recomputed.books_replacement_cost - packet.totals.books_replacement_cost) > 0.01:
        out.append(ReviewItem(ref_id="totals",
                              reason="checker: book totals do not match a code recomputation"))
    if recomputed.book_count != packet.totals.book_count:
        out.append(ReviewItem(ref_id="totals", reason="checker: book_count mismatch on recompute"))

    # 2. Any price MUST carry a retrievable source — no URL/date = fabricated price.
    for b in packet.books:
        rc, uv = b.replacement_cost, b.used_value
        if rc.amount and not (rc.url and rc.retrieved_at):
            out.append(ReviewItem(ref_id=b.id, reason="checker: replacement price has no source URL/date"))
        if uv.amount and not (uv.url and uv.retrieved_at):
            out.append(ReviewItem(ref_id=b.id, reason="checker: used price has no source URL/date"))
    for it in packet.items:
        rc = it.replacement_cost
        if (rc.low or rc.high) and not (rc.url and rc.retrieved_at):
            out.append(ReviewItem(ref_id=it.id, reason="checker: item price has no source URL/date"))

    # 3. Identified lines should be traceable to a frame.
    for b in packet.books:
        if b.status == "identified" and not b.frame_ref:
            out.append(ReviewItem(ref_id=b.id, reason="checker: identified book has no evidence frame_ref"))

    # 4. Art must not be auto-priced.
    for it in packet.items:
        if it.status == "needs_appraisal" and (it.replacement_cost.low or it.replacement_cost.high):
            out.append(ReviewItem(ref_id=it.id, reason="checker: appraisal item was auto-priced (should be blank)"))

    # 5. Coarse sanity on the room.
    r = packet.room
    if r.floor_area_m2 and (r.floor_area_m2 < 1.0 or r.floor_area_m2 > 500.0):
        out.append(ReviewItem(ref_id="room", reason=f"checker: floor area {r.floor_area_m2} m² is implausible"))

    return out
