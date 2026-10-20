"""End-to-end pipeline tests.

Exercises the whole post-sweep flow — ingest a bundle, run all four stages, assemble the
packet, render the report — with the external calls (vision for Stage A/C, Amazon for
Stage D) MOCKED so the test is deterministic and offline. Asserts the invariants the brief
cares about: totals computed in code, unknowns excluded (not fabricated), art → appraisal,
unreadable spines skipped, room measured from planes, every figure traceable.
"""

import numpy as np

from fastapi.testclient import TestClient

from backend.app import app, store as app_store
from backend.ingest.capture_schema import CaptureFrame, FrameQuality, PlaneAnchor, SweepMeta
from backend.ingest.store import SweepStore
from backend.pipeline import run_pipeline
from backend.report.render import render_report_html

I4 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
K9 = [1000, 0, 480, 0, 1000, 360, 0, 0, 1]


def _ceiling_transform(y: float) -> list[float]:
    m = np.eye(4); m[1, 3] = y
    return m.flatten().tolist()


def _rect(w: float, d: float) -> list[list[float]]:
    return [[0, 0, 0], [w, 0, 0], [w, 0, d], [0, 0, d]]


def _frame(ts: float) -> CaptureFrame:
    return CaptureFrame(timestamp=ts, image="x", image_width=960, image_height=720,
                        intrinsics=K9, transform=I4, quality=FrameQuality(tracking_state="normal"))


def _seed_sweep(store: SweepStore, sid: str) -> None:
    """Save a couple of frames + finalize a 3x4x2.5 m room."""
    for ts in (0.1, 0.2):
        store.save_frame(sid, _frame(ts), b"\xff\xd8\xff\xd9")
    planes = [
        PlaneAnchor(identifier="floor", alignment="horizontal", classification="floor",
                    transform=I4, center=[0, 0, 0], extent=[3, 4], boundary_vertices=_rect(3, 4)),
        PlaneAnchor(identifier="ceil", alignment="horizontal", classification="ceiling",
                    transform=_ceiling_transform(2.5), center=[0, 0, 0], extent=[3, 4],
                    boundary_vertices=_rect(3, 4)),
    ]
    store.finalize(sid, SweepMeta(id=sid, country="IN", currency="INR"), planes)


def _mock_stages(monkeypatch):
    # Stage A: one legible book seen in TWO frames (corroborated → identified) + one unreadable
    # spine (must be skipped, never guessed).
    monkeypatch.setattr("backend.pipeline.identify_books", lambda *a, **k: [
        {"title": "Dune", "author": "Frank Herbert", "legible": True, "confidence": 0.92, "frame_ref": "frames/0.100.jpg"},
        {"title": "Dune", "author": "Frank Herbert", "legible": True, "confidence": 0.90, "frame_ref": "frames/0.150.jpg"},
        {"legible": False, "confidence": 0.1, "frame_ref": "frames/0.200.jpg"},
    ])
    # Stage C: a lamp (priceable) + a portrait (art -> needs_appraisal, never auto-priced).
    monkeypatch.setattr("backend.pipeline.detect_items", lambda *a, **k: [
        {"category": "lamp", "description": "black desk lamp", "brand_model": "", "is_art": False, "confidence": 0.8, "frame_ref": "frames/0.100.jpg"},
        {"category": "portrait", "description": "framed portrait", "brand_model": "", "is_art": True, "confidence": 0.7, "frame_ref": "frames/0.200.jpg"},
    ])
    # Stage D: deterministic sourced prices.
    monkeypatch.setattr("backend.pipeline.price_book", lambda *a, **k: {
        "replacement_cost": {"amount": 499.0, "source": "amazon", "url": "http://a/dp/1", "retrieved_at": "2026-10-17", "converted": False},
        "used_value": {"amount": 200.0, "source": "amazon", "url": "http://a/dp/1", "retrieved_at": "2026-10-17", "condition_assumed": "good"},
    })
    monkeypatch.setattr("backend.pipeline.price_item", lambda *a, **k: {
        "replacement_cost": {"low": 1200.0, "high": 2400.0, "source": "amazon", "url": "http://a/dp/2", "retrieved_at": "2026-10-17"},
    })
    monkeypatch.setattr("backend.pipeline._PRICING", True)


def test_full_pipeline_populates_packet(tmp_path, monkeypatch):
    store = SweepStore(tmp_path)
    _seed_sweep(store, "e2e1")
    _mock_stages(monkeypatch)

    packet = run_pipeline("e2e1", store, api_key="test")

    # Stage A: one identified book + the unreadable spine logged as unidentified (not dropped).
    assert packet.totals.books_identified == 1
    assert packet.totals.books_unidentified == 1
    assert packet.totals.book_count == 2
    ident = next(b for b in packet.books if b.status == "identified")
    assert ident.title == "Dune"
    assert ident.frame_ref == "frames/0.100.jpg"  # traceable

    # Stage D: book priced from a real source; totals summed IN CODE.
    assert packet.books[0].replacement_cost.amount == 499.0
    assert packet.books[0].replacement_cost.url == "http://a/dp/1"
    assert packet.totals.books_replacement_cost == 499.0
    assert packet.totals.books_used_value == 200.0

    # Stage B: room measured from planes (3x4=12 m², height 2.5, walls = 14*2.5=35).
    assert packet.room.floor_area_m2 == 12.0
    assert packet.room.height_m == 2.5
    assert abs(packet.room.wall_area_m2 - 35.0) < 0.01

    # Stage C: lamp priced as a range; portrait -> needs_appraisal, NOT priced.
    lamp = next(i for i in packet.items if i.category == "lamp")
    portrait = next(i for i in packet.items if i.category == "portrait")
    assert lamp.replacement_cost.low == 1200.0 and lamp.status == "range"
    assert portrait.status == "needs_appraisal"
    assert portrait.replacement_cost.low is None  # art never auto-priced
    assert packet.totals.items_replacement_cost_low == 1200.0

    # Review queue flags the art for a human.
    assert any("appraisal" in r.reason.lower() for r in packet.review_queue)

    # Packet persisted; report renders real content from it.
    assert store.packet_path("e2e1").exists()
    html = render_report_html(packet, "e2e1")
    assert "Dune" in html and "desk lamp" in html and "framed portrait" in html


def test_unknown_price_excluded_not_fabricated(tmp_path, monkeypatch):
    store = SweepStore(tmp_path)
    _seed_sweep(store, "e2e2")
    _mock_stages(monkeypatch)
    # Pricing finds nothing -> amounts None, no URL. Must be excluded, not zeroed.
    monkeypatch.setattr("backend.pipeline.price_book", lambda *a, **k: {
        "replacement_cost": {"amount": None, "source": "", "url": "", "retrieved_at": "2026-10-17", "converted": False},
        "used_value": {"amount": None, "source": "", "url": "", "retrieved_at": "2026-10-17", "condition_assumed": ""},
    })

    packet = run_pipeline("e2e2", store, api_key="test")
    assert packet.books[0].replacement_cost.amount is None
    assert packet.totals.books_replacement_cost == 0.0
    assert packet.totals.excluded_from_totals >= 1
    # An identified-but-unpriced book must be surfaced for review, not silently dropped.
    assert any(r.ref_id == packet.books[0].id for r in packet.review_queue)


def test_http_finalize_then_report_lifecycle(tmp_path, monkeypatch):
    """Ingest over HTTP, then the report shows 'processing' until a packet exists."""
    monkeypatch.setattr(app_store, "root", tmp_path)
    client = TestClient(app)

    # Upload a frame.
    import json
    meta = json.dumps({"timestamp": 0.1, "image": "x", "image_width": 960, "image_height": 720,
                       "intrinsics": K9, "transform": I4, "quality": {"tracking_state": "normal"}})
    r = client.post("/sweeps/http1/frame", data={"metadata": meta},
                    files={"image": ("f.jpg", b"\xff\xd8\xff\xd9", "image/jpeg")})
    assert r.status_code == 200

    # Finalize (background pipeline will run with the real — unkeyed — stages and fail
    # gracefully; we only assert the bundle + report wiring here).
    r = client.post("/sweeps/http1/finalize",
                    json={"meta": {"id": "http1", "country": "IN", "currency": "INR"}, "planes": []})
    assert r.status_code == 200 and r.json()["frames"] == 1

    # Report renders (before a packet exists -> processing banner + auto-refresh).
    rep = client.get("/sweeps/http1/report")
    assert rep.status_code == 200
    assert "Contents Claim Packet" in rep.text
