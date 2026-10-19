"""Stage C — detect and classify non-book contents from captured frames (Gemini/Vertex).

Mirrors Stage A: a vision model lists non-book items (furniture, lamps, art, electronics,
decor) per sampled frame. Art/portraits are marked needs_appraisal unless clearly a print —
the agent is expected to ask, and we never auto-value art. Deduped by description downstream.
"""

from __future__ import annotations

import os

from backend.ingest.store import SweepStore
from backend.vision import vision_extract

MAX_FRAMES = int(os.environ.get("STAGE_C_MAX_FRAMES", "8"))

_PROMPT = (
    "Catalogue NON-BOOK contents in this photo for an insurance inventory: shelving, "
    "furniture, lamps, framed art/portraits, rugs, electronics, appliances, decor. "
    "For each distinct item give: category, a short description, brand_model if legible "
    "(else ''), and is_art=true for paintings/portraits/framed pictures. Do not list books. "
    "Catalogue only PHYSICAL objects in the room. The laptop/monitor/TV itself is a valid item, "
    "but IGNORE whatever is shown ON its screen (apps, images, products, books on a web page) — "
    "on-screen content is not physical inventory. Likewise ignore items pictured in a poster/photo. "
    'Return strict JSON: {"items":[{"category":"","description":"","brand_model":"",'
    '"is_art":false,"confidence":0.0}]}'
)


def _select_frames(frames: list) -> list:
    if len(frames) <= MAX_FRAMES:
        return frames
    step = len(frames) / MAX_FRAMES
    return [frames[int(i * step)] for i in range(MAX_FRAMES)]


def detect_items(sweep_id: str, store: SweepStore, api_key: str = "") -> list[dict]:
    """Return raw per-item detections: [{category, description, brand_model, is_art,
    confidence, frame_ref}] (or {error, frame_ref}). ``api_key`` unused with Gemini (ADC)."""
    bundle = store.load_bundle(sweep_id)
    out: list[dict] = []
    for fr in _select_frames(bundle.frames):
        try:
            data = vision_extract(_PROMPT, store.frame_path(sweep_id, fr.image).read_bytes())
            for it in data.get("items", []):
                out.append({**it, "frame_ref": fr.image})
        except Exception as exc:  # noqa: BLE001
            out.append({"error": str(exc), "frame_ref": fr.image})
    return out
