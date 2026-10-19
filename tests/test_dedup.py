"""Fuzzy dedup: the same book read differently across frames must collapse to one."""

import backend.pipeline as P


def test_match_index_merges_ocr_variants():
    norms = ["theupstarts"]
    assert P._match_index("upstarts", norms) >= 0     # substring
    assert P._match_index("upstaris", ["upstarts"]) >= 0   # fuzzy OCR misread
    assert P._match_index("dune", norms) == -1        # distinct


def test_stage_a_dedups_same_book_across_views(monkeypatch):
    raw = [
        {"title": "The Upstarts", "author": "Brad Stone", "legible": True, "confidence": 0.9, "frame_ref": "f1"},
        {"title": "upstarts", "legible": True, "confidence": 0.6, "frame_ref": "f2"},
        {"title": "upstaris", "legible": True, "confidence": 0.5, "frame_ref": "f3"},
        {"title": "Dune", "author": "Herbert", "legible": True, "confidence": 0.95, "frame_ref": "f4"},
    ]
    from backend.ingest.capture_schema import CaptureBundle, SweepMeta
    monkeypatch.setattr(P, "identify_books", lambda *a, **k: raw)
    bundle = CaptureBundle(meta=SweepMeta(id="s"), frames=[], planes=[])
    books = P._stage_a_books("s", None, "", bundle, 1.0)
    titles = [b.title for b in books if b.status == "identified"]
    assert len(titles) == 2, titles                    # Upstarts variants merged + Dune
    assert any("Upstarts" in t for t in titles)        # kept the best (highest-confidence) read
    assert "Dune" in titles
