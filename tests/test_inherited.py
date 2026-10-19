"""Tests for the patterns inherited from the reference app:
evidence manifest, live-sync observations, and the independent checker agent."""

from backend.assembler import assemble_packet
from backend.ingest.store import SweepStore
from backend.review.checker import audit_packet
from backend.schema import (
    AgentObservation,
    Book,
    Item,
    ReplacementCost,
    Room,
    Sweep,
)


def _identified(bid, url="http://a/dp/1"):
    return Book(id=bid, status="identified", title=f"Book {bid}", frame_ref=f"frames/{bid}.jpg",
                replacement_cost=ReplacementCost(amount=100.0, source="amazon", url=url, retrieved_at="2026-10-18"))


# --- Evidence manifest (pin_evidence_photo analog) ---

def test_evidence_manifest_links_every_line_to_a_frame():
    books = [_identified("b1")]
    items = [Item(id="i1", category="lamp", description="desk lamp", frame_ref="frames/i1.jpg")]
    packet = assemble_packet(Sweep(), Room(), books, items, 0.0)
    assert len(packet.evidence) == 2
    by_ref = {e.ref_id: e for e in packet.evidence}
    assert by_ref["b1"].frame_ref == "frames/b1.jpg" and by_ref["b1"].kind == "book"
    assert by_ref["i1"].frame_ref == "frames/i1.jpg" and by_ref["i1"].kind == "item"


# --- Checker agent (fraud/safety gate analog) ---

def test_checker_flags_price_without_source():
    # amount set but url missing => fabricated-price risk, must be flagged.
    bad = _identified("b1", url="")
    packet = assemble_packet(Sweep(), Room(), [bad], [], 0.0)
    flags = audit_packet(packet)
    assert any("no source" in f.reason.lower() and f.ref_id == "b1" for f in flags)


def test_checker_passes_clean_sourced_price():
    packet = assemble_packet(Sweep(), Room(), [_identified("b1")], [], 0.0)
    flags = audit_packet(packet)
    assert not any("no source" in f.reason.lower() for f in flags)


def test_checker_flags_autopriced_art():
    from backend.schema import ItemReplacementCost
    art = Item(id="i1", category="portrait", description="framed portrait", status="needs_appraisal",
               frame_ref="frames/i1.jpg",
               replacement_cost=ItemReplacementCost(low=500.0, high=900.0, source="amazon",
                                                    url="http://a", retrieved_at="2026-10-18"))
    packet = assemble_packet(Sweep(), Room(), [], [art], 0.0)
    flags = audit_packet(packet)
    assert any("auto-priced" in f.reason.lower() for f in flags)


def test_checker_flags_implausible_room():
    packet = assemble_packet(Sweep(), Room(floor_area_m2=0.03), [], [], 0.0)
    flags = audit_packet(packet)
    assert any("implausible" in f.reason.lower() for f in flags)


# --- Live packet sync (sync_claim_packet analog) ---

def test_observation_store_roundtrip(tmp_path):
    store = SweepStore(tmp_path)
    store.append_observation("s1", {"kind": "book", "label": "orange Penguins", "note": "", "frame_ref": "frames/1.jpg", "at": 2.0})
    obs = store.load_observations("s1")
    assert len(obs) == 1 and obs[0]["label"] == "orange Penguins"


def test_agent_observations_flow_into_packet():
    obs = [AgentObservation(kind="shelf", label="orange Penguin paperbacks", frame_ref="frames/1.jpg", at=2.0)]
    packet = assemble_packet(Sweep(), Room(), [], [], 0.0, agent_observations=obs)
    assert packet.agent_observations[0].label == "orange Penguin paperbacks"
