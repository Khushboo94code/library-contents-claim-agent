"""Stage A — detect book spines (with bounding boxes) and read titles (Gemini/Vertex).

Returns EVERY genuine book spine with a normalized bounding box + legibility, so downstream
can (a) count books incl. unreadable ones, (b) measure each spine's cm from its box, and
(c) read titles for the legible ones. The prompt is strict about what counts as a book —
notebooks, files, boxes, magazines, electronics and decor are excluded — to avoid tagging
random objects as books. Unreadable spines get legible=false and NO guessed title.
"""

from __future__ import annotations

import os

from backend.ingest.store import SweepStore
from backend.vision import vision_extract

MAX_FRAMES = int(os.environ.get("STAGE_A_MAX_FRAMES", "8"))

_PROMPT = (
    "You are inventorying a BOOKSHELF from ONE photo. Identify ONLY actual published books "
    "that show a book spine or cover. STRICTLY EXCLUDE anything that is not a published book: "
    "notebooks, diaries, exercise books, files, folders, binders, magazines, boxes, board games, "
    "electronics, picture frames, and decor — do not list those at all. "
    "CRITICAL: only count PHYSICAL books on a real shelf. IGNORE any book that appears ON a screen "
    "(a laptop, monitor, phone, tablet, TV, or a photo/poster of books) — screen or printed images "
    "of books are NOT physical inventory and must NOT be listed. "
    "For each real book give a bounding box. If the printed title is CLEARLY legible, give the "
    "title and author; if it is clearly a book but you cannot read the title, set legible=false "
    "and do NOT guess a title. Never invent titles. "
    'Return strict JSON only: {"books":[{"bbox":[x0,y0,x1,y1],"legible":true,"title":"",'
    '"author":"","confidence":0.0}]} where bbox is normalized 0..1 (x0,y0 = top-left, '
    "x1,y1 = bottom-right) and confidence 0..1 is how sure you are of the title text."
)


def _select_frames(frames: list) -> list:
    if len(frames) <= MAX_FRAMES:
        return frames
    step = len(frames) / MAX_FRAMES
    return [frames[int(i * step)] for i in range(MAX_FRAMES)]


def identify_books(sweep_id: str, store: SweepStore, api_key: str = "") -> list[dict]:
    """Return raw per-spine detections across sampled frames:
    [{bbox, legible, title, author, confidence, frame_ref}] (or {error, frame_ref})."""
    bundle = store.load_bundle(sweep_id)
    out: list[dict] = []
    for fr in _select_frames(bundle.frames):
        try:
            data = vision_extract(_PROMPT, store.frame_path(sweep_id, fr.image).read_bytes())
            for b in data.get("books", []):
                out.append({**b, "frame_ref": fr.image})
        except Exception as exc:  # noqa: BLE001 - record per-frame failures, keep going
            out.append({"error": str(exc), "frame_ref": fr.image})
    return out
