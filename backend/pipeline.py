"""Post-sweep orchestrator — runs all stages on a finalized sweep and writes packet.json.

Stage A  identification (vision) ........ books: title/author
Stage B  measurement (planes + fiducial)  room: floor/wall area, dims
Stage C  non-book detection (vision) ..... items: furniture/art/electronics
Stage D  pricing (scraping repo) ......... local new/used prices with source URLs
Totals are computed in code by the assembler. Every stage degrades gracefully: if it can't
run (no planes, no network, no key), it leaves its fields blank rather than fabricating.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

from backend.assembler import assemble_packet
from backend.geometry.scale import compute_scale
from backend.ingest.store import SweepStore
from backend.items.detect import detect_items
from backend.review.checker import audit_packet
from backend.room.planes import measure_room
from backend.schema import (
    AgentObservation,
    Book,
    ClaimPacket,
    Item,
    ItemDimensions,
    ItemReplacementCost,
    ReplacementCost,
    Room,
    Sweep,
    UsedValue,
)
from backend.geometry.camera import mat3
from backend.geometry.fiducial import load_image
from backend.spines.identify import identify_books
from backend.spines.measure import fiducial_plane_camera, measure_spine, measure_spine_fiducial

# A legible spine below this title-reading confidence is treated as unidentified rather than
# an identified book — stops low-confidence guesses / random objects showing as titled books.
BOOK_CONFIDENCE_FLOOR = float(os.environ.get("BOOK_CONFIDENCE_FLOOR", "0.45"))

# Stage D is optional until the pricing module is present.
try:
    from backend.pricing.price import price_book, price_item
    _PRICING = True
except Exception:  # noqa: BLE001
    _PRICING = False

# Above this (in local currency) auto-pricing is withheld pending human appraisal.
APPRAISAL_THRESHOLD = float(os.environ.get("APPRAISAL_THRESHOLD", "15000"))


def _norm(s: str) -> str:
    t = re.sub(r"^(the|a|an)\s+", "", (s or "").lower().strip())  # drop leading articles
    return re.sub(r"[^a-z0-9]", "", t)


# Titles read across frames vary by OCR ("upstarts" vs "upstaris") — merge near-matches.
TITLE_SIMILARITY = float(os.environ.get("DEDUP_SIMILARITY", "0.84"))


def _match_index(key: str, norms: list[str], threshold: float = TITLE_SIMILARITY) -> int:
    """Index of an already-accepted normalized string that's the same book, or -1.
    Matches exact, substring (truncated reads), or high fuzzy ratio."""
    for i, n in enumerate(norms):
        if not n or not key:
            continue
        if n == key or n in key or key in n or SequenceMatcher(None, n, key).ratio() >= threshold:
            return i
    return -1


def _stage_a_books(sweep_id, store, api_key, bundle, scale: float) -> list[Book]:
    raw = identify_books(sweep_id, store, api_key)
    frames = {f.image: f for f in bundle.frames}
    planes = bundle.planes

    acc: list[dict] = []       # identified, fuzzy-deduped
    norms: list[str] = []
    unreadable = defaultdict(list)  # frame_ref -> [spine dicts] (book-shaped, title unreadable)

    marker_cm = bundle.meta.fiducial_marker_cm
    dict_name = bundle.meta.fiducial_dict or "DICT_4X4_50"
    fid_cache: dict[str, object] = {}  # frame_ref -> (pt, n) | False (tried, none)

    def _dims_for(bbox, frame, fref):
        """Spine cm via ARKit plane, else the fiducial marker (detected once per frame)."""
        if not (bbox and frame):
            return (None, None)
        d = measure_spine(bbox, frame, planes, scale)
        if d[0] is not None or not marker_cm:
            return d
        if fref not in fid_cache:
            try:
                img = load_image(str(store.frame_path(sweep_id, fref)))
                fid_cache[fref] = fiducial_plane_camera(img, mat3(frame.intrinsics), marker_cm, dict_name) or False
            except Exception:  # noqa: BLE001
                fid_cache[fref] = False
        plane = fid_cache[fref]
        if not plane:
            return (None, None)
        return measure_spine_fiducial(bbox, frame, plane[0], plane[1], scale)

    for r in raw:
        if r.get("error"):
            continue
        fref = r.get("frame_ref", "")
        bbox, frame = r.get("bbox"), frames.get(fref)
        dims = _dims_for(bbox, frame, fref)
        title = (r.get("title") or "").strip()
        conf = float(r.get("confidence") or 0)
        legible = bool(r.get("legible", True)) and bool(title) and conf >= BOOK_CONFIDENCE_FLOOR

        if not legible:
            unreadable[fref].append({"frame_ref": fref, "dims": dims})
            continue
        key = _norm(title)
        if not key:
            unreadable[fref].append({"frame_ref": fref, "dims": dims})
            continue
        idx = _match_index(key, norms)
        if idx >= 0:
            cur = acc[idx]
            if conf > (cur["conf"] or 0) or (len(title) > len(cur["title"]) and conf >= (cur["conf"] or 0)):
                cur.update(title=title, author=(r.get("author") or "").strip() or cur["author"],
                           conf=conf, frame_ref=fref)
                norms[idx] = key
            if dims[0] and not cur["dims"][0]:
                cur["dims"] = dims
            continue
        acc.append({"title": title, "author": (r.get("author") or "").strip(),
                    "conf": conf, "frame_ref": fref, "dims": dims})
        norms.append(key)

    books = [Book(id=f"b{i+1}", status="identified", title=a["title"], author=a["author"],
                  frame_ref=a["frame_ref"], id_confidence=round(a["conf"], 2),
                  spine_height_cm=a["dims"][0], spine_thickness_cm=a["dims"][1])
             for i, a in enumerate(acc)]

    # Unidentified: conservative count = the single frame with the most unreadable book
    # spines (cross-frame dedup of unreadable spines isn't possible without poses; summing
    # would overcount). Logged WITH dimensions + frame_ref, never a guessed title.
    if unreadable:
        best = max(unreadable.values(), key=len)
        for j, u in enumerate(best):
            books.append(Book(id=f"u{j+1}", status="unidentified", frame_ref=u["frame_ref"],
                              spine_height_cm=u["dims"][0], spine_thickness_cm=u["dims"][1]))
    return books


def _stage_c_items(sweep_id, store, api_key) -> list[Item]:
    raw = detect_items(sweep_id, store, api_key)
    items: list[Item] = []
    norms: list[str] = []
    for r in raw:
        if r.get("error"):
            continue
        desc = (r.get("description") or "").strip()
        key = _norm(desc)
        if not desc or not key:
            continue
        if _match_index(key, norms) >= 0:   # same item seen from another view
            continue
        norms.append(key)
        status = "needs_appraisal" if r.get("is_art") else "range"
        conf = r.get("confidence")
        items.append(Item(id=f"i{len(items)+1}", category=(r.get("category") or "").strip(),
                          description=desc, brand_model=(r.get("brand_model") or "").strip(),
                          frame_ref=r.get("frame_ref", ""), status=status,
                          dimensions_cm=ItemDimensions(),
                          confidence=float(conf) if conf else None))
    return items


_PRICE_WORKERS = int(os.environ.get("PRICE_WORKERS", "8"))


def _price_books(books: list[Book], country: str, currency: str) -> None:
    """Price identified books CONCURRENTLY — network-bound calls run in parallel so the
    pipeline finishes in ~one slow call, not the sum of all of them."""
    targets = [b for b in books if b.status == "identified"]

    def _one(b: Book):
        try:
            return b, price_book(b.title, b.author, b.isbn, country, currency)
        except Exception:  # noqa: BLE001
            return b, None

    with ThreadPoolExecutor(max_workers=_PRICE_WORKERS) as ex:
        for b, res in ex.map(_one, targets):
            if not res:
                continue
            rc, uv = res.get("replacement_cost", {}), res.get("used_value", {})
            b.replacement_cost = ReplacementCost(**rc) if rc else b.replacement_cost
            b.used_value = UsedValue(**uv) if uv else b.used_value
            if b.replacement_cost.amount and b.replacement_cost.amount > APPRAISAL_THRESHOLD:
                b.status = "needs_appraisal"


def _price_items(items: list[Item], country: str, currency: str) -> None:
    targets = [it for it in items if it.status != "needs_appraisal"]

    def _one(it: Item):
        try:
            return it, price_item(it.description, it.category, country, currency)
        except Exception:  # noqa: BLE001
            return it, None

    with ThreadPoolExecutor(max_workers=_PRICE_WORKERS) as ex:
        for it, res in ex.map(_one, targets):
            rc = (res or {}).get("replacement_cost", {})
            if rc:
                it.replacement_cost = ItemReplacementCost(**rc)
                if it.replacement_cost.low is not None:
                    it.status = "priced" if it.replacement_cost.low == it.replacement_cost.high else "range"


def run_pipeline(sweep_id: str, store: SweepStore, api_key: str) -> ClaimPacket:
    bundle = store.load_bundle(sweep_id)
    m = bundle.meta
    sweep = Sweep(id=m.id, captured_at=m.captured_at, device=m.device,
                  duration_s=m.duration_s, country=m.country, currency=m.currency)

    scale, method, sconf = compute_scale(bundle, store, sweep_id)
    books = _stage_a_books(sweep_id, store, api_key, bundle, scale)   # Stage A (+ spine dims)
    room = measure_room(bundle, scale, method)               # Stage B
    room.confidence = sconf if room.confidence is None else round((room.confidence + sconf) / 2, 2)
    items = _stage_c_items(sweep_id, store, api_key)          # Stage C
    # Shelf run = total linear metres of spine thickness we could measure.
    shelf_run_m = round(sum((b.spine_thickness_cm or 0) for b in books) / 100.0, 3)

    if _PRICING:                                             # Stage D (if available)
        _price_books(books, m.country or "IN", m.currency or "INR")
        _price_items(items, m.country or "IN", m.currency or "INR")

    # Live packet sync: fold in whatever the agent logged during the sweep.
    observations = [AgentObservation(**o) for o in store.load_observations(sweep_id)]

    packet = assemble_packet(sweep, room, books, items, shelf_run_m=shelf_run_m,
                             agent_observations=observations)

    # Independent checker agent: audit the finished packet, append disagreements.
    packet.review_queue.extend(audit_packet(packet))

    store.write_packet(sweep_id, packet.model_dump_json(indent=2))
    return packet
