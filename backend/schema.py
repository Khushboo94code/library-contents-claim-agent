"""Pydantic models for the claim packet output contract.

This mirrors the required `claim_packet.json` shape from the brief exactly. Two
invariants are enforced by design, not convention:

1.  **An empty field means "the system does not know."** Unknown values are
    ``None`` and serialize to ``null`` — never a fabricated ``0`` or ``""``.
    A filled field with no evidence behind it is a defect (brief, rule 1).
2.  **Totals are never written by a language model.** They are computed in code
    by :func:`backend.assembler.compute_totals` from the line items.

You may add fields; you may not drop the ones the contract specifies.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

BookStatus = Literal["identified", "unidentified", "needs_appraisal"]
ItemStatus = Literal["priced", "range", "needs_appraisal"]


class Sweep(BaseModel):
    id: str = ""
    captured_at: str = ""  # ISO-8601
    device: str = ""
    duration_s: float = 0.0
    country: str = ""  # ISO-3166 alpha-2, e.g. "IN"
    currency: str = ""  # ISO-4217, e.g. "INR"


class Room(BaseModel):
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    floor_area_m2: Optional[float] = None
    wall_area_m2: Optional[float] = None
    shelved_wall_area_m2: Optional[float] = None
    scale_method: str = ""  # e.g. "arkit_world_tracking+fiducial_scale_correction"
    confidence: Optional[float] = None


class ReplacementCost(BaseModel):
    """New / equivalent-today price for a book. Amount is None when unknown."""

    amount: Optional[float] = None
    source: str = ""
    url: str = ""
    retrieved_at: str = ""  # ISO-8601 date the price was fetched
    converted: bool = False  # True when converted from another market's currency


class UsedValue(BaseModel):
    amount: Optional[float] = None
    source: str = ""
    url: str = ""
    retrieved_at: str = ""
    condition_assumed: str = ""


class Book(BaseModel):
    id: str = ""
    shelf: str = ""
    position: Optional[int] = None
    frame_ref: str = ""  # MUST point to a saved frame / timestamp we can open
    status: BookStatus = "unidentified"
    title: str = ""
    author: str = ""
    edition: str = ""
    isbn: str = ""
    spine_height_cm: Optional[float] = None
    spine_thickness_cm: Optional[float] = None
    id_confidence: Optional[float] = None
    replacement_cost: ReplacementCost = Field(default_factory=ReplacementCost)
    used_value: UsedValue = Field(default_factory=UsedValue)


class ItemDimensions(BaseModel):
    w: Optional[float] = None
    h: Optional[float] = None
    d: Optional[float] = None


class ItemReplacementCost(BaseModel):
    """Non-book items are priced as a range (low/high)."""

    low: Optional[float] = None
    high: Optional[float] = None
    source: str = ""
    url: str = ""
    retrieved_at: str = ""


class Item(BaseModel):
    id: str = ""
    category: str = ""
    description: str = ""
    brand_model: str = ""
    frame_ref: str = ""
    dimensions_cm: ItemDimensions = Field(default_factory=ItemDimensions)
    status: ItemStatus = "range"
    replacement_cost: ItemReplacementCost = Field(default_factory=ItemReplacementCost)
    confidence: Optional[float] = None


class Totals(BaseModel):
    """Computed in code only — see :func:`backend.assembler.compute_totals`."""

    book_count: int = 0
    books_identified: int = 0
    books_unidentified: int = 0
    shelf_run_m: float = 0.0
    books_replacement_cost: float = 0.0
    books_used_value: float = 0.0
    items_replacement_cost_low: float = 0.0
    items_replacement_cost_high: float = 0.0
    excluded_from_totals: int = 0


class ReviewItem(BaseModel):
    ref_id: str
    reason: str


class EvidenceItem(BaseModel):
    """A packet line linked to the saved frame that supports it (the evidence manifest).
    Inherited from the reference app's pin_evidence_photo — makes every figure traceable."""

    ref_id: str          # book/item id this evidence supports
    frame_ref: str       # saved frame / timestamp an adjuster can open
    kind: str = ""       # "book" | "item"
    label: str = ""      # human label for the manifest
    verified: bool = False


class AgentObservation(BaseModel):
    """Something the live agent logged during the sweep (its sync_claim_packet analog).
    Persisted so the packet reflects what was said in the moment, with a frame_ref."""

    kind: str = ""       # book | item | shelf | note
    label: str = ""
    note: str = ""
    frame_ref: str = ""
    at: float = 0.0      # seconds into the sweep


class ClaimPacket(BaseModel):
    sweep: Sweep = Field(default_factory=Sweep)
    room: Room = Field(default_factory=Room)
    books: list[Book] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)
    review_queue: list[ReviewItem] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    agent_observations: list[AgentObservation] = Field(default_factory=list)
