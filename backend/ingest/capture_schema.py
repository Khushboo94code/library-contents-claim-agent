"""The capture bundle the iOS app uploads — the contract between native and backend.

The iOS layer stays thin: it records an ARKit session and uploads this bundle.
All measurement happens on the backend from these numbers, so every field here
is something the backend genuinely needs. Matrices are row-major 4x4 lists
(16 floats) to keep the JSON trivial to emit from Swift and parse in numpy.

Coordinate conventions (critical — see backend/geometry/camera.py):
* ``transform`` fields are ARKit's camera-to-world / anchor-to-world matrices.
* ARKit camera space is +X right, +Y up, -Z forward (looks down -Z).
* ``intrinsics`` is the 3x3 K for the frame's ``image`` at ``image_width`` x
  ``image_height``. If you downscale the image before upload, downscale K too.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

Mat4 = list[float]  # 16 floats, row-major
Mat3 = list[float]  # 9 floats, row-major


class FrameQuality(BaseModel):
    """Cheap on-device signals used to direct the sweep live."""

    blur_score: Optional[float] = None  # Laplacian variance; lower = blurrier
    tracking_state: str = ""  # "normal" | "limited:<reason>" | "notAvailable"
    exposure_offset: Optional[float] = None


class CaptureFrame(BaseModel):
    timestamp: float  # seconds, monotonic within the sweep
    image: str  # path/key of the saved JPEG (becomes frame_ref in the packet)
    image_width: int
    image_height: int
    intrinsics: Mat3  # K for image_width x image_height
    transform: Mat4  # camera-to-world (ARKit camera.transform)
    quality: FrameQuality = Field(default_factory=FrameQuality)


class PlaneAnchor(BaseModel):
    """An ARPlaneAnchor captured at its final (post-merge) state."""

    identifier: str
    alignment: str  # "horizontal" | "vertical"
    classification: str = ""  # ARKit: floor|wall|ceiling|table|seat|window|door|none
    transform: Mat4  # plane-to-world; plane-local normal is +Y
    center: list[float]  # [x, y, z] in plane-local coords
    extent: list[float]  # [width, height] (plane-local X, Z spans)
    boundary_vertices: list[list[float]]  # [[x, y, z], ...] plane-local polygon


class SweepMeta(BaseModel):
    id: str = ""
    captured_at: str = ""
    device: str = ""
    duration_s: float = 0.0
    country: str = ""
    currency: str = ""
    fiducial_marker_cm: Optional[float] = None  # known marker edge length, if used
    fiducial_dict: str = ""


class CaptureBundle(BaseModel):
    """Everything one sweep uploads."""

    meta: SweepMeta = Field(default_factory=SweepMeta)
    frames: list[CaptureFrame] = Field(default_factory=list)
    planes: list[PlaneAnchor] = Field(default_factory=list)
