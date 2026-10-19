"""Synthetic room-measurement tests — a known 3m x 4m x 2.5m room must measure correctly."""

import numpy as np

from backend.ingest.capture_schema import CaptureBundle, PlaneAnchor, SweepMeta
from backend.room.planes import measure_room

I4 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]  # identity (row-major)


def _translate(y: float) -> list[float]:
    m = np.eye(4); m[1, 3] = y
    return m.flatten().tolist()


def _rect(w: float, d: float) -> list[list[float]]:
    return [[0, 0, 0], [w, 0, 0], [w, 0, d], [0, 0, d]]  # plane-local X-Z rectangle


def _bundle(planes):
    return CaptureBundle(meta=SweepMeta(id="t", country="IN", currency="INR"), frames=[], planes=planes)


def test_rectangular_room_floor_and_wall_area():
    planes = [
        PlaneAnchor(identifier="floor", alignment="horizontal", classification="floor",
                    transform=I4, center=[0, 0, 0], extent=[3, 4], boundary_vertices=_rect(3, 4)),
        PlaneAnchor(identifier="ceil", alignment="horizontal", classification="ceiling",
                    transform=_translate(2.5), center=[0, 0, 0], extent=[3, 4], boundary_vertices=_rect(3, 4)),
    ]
    room = measure_room(_bundle(planes), scale=1.0)
    assert room.floor_area_m2 == 12.0                 # 3 x 4
    assert room.length_m == 4.0 and room.width_m == 3.0
    assert room.height_m == 2.5                       # ceiling - floor
    # wall area = perimeter(14) x height(2.5) = 35
    assert abs(room.wall_area_m2 - 35.0) < 0.01
    assert room.confidence and room.confidence >= 0.6


def test_scale_factor_applied():
    planes = [PlaneAnchor(identifier="floor", alignment="horizontal", classification="floor",
                          transform=I4, center=[0, 0, 0], extent=[3, 4], boundary_vertices=_rect(3, 4))]
    room = measure_room(_bundle(planes), scale=2.0)   # areas scale by 2^2
    assert room.floor_area_m2 == 48.0                 # 12 * 4
    assert room.length_m == 8.0                       # 4 * 2


def test_no_planes_is_blank_not_fabricated():
    room = measure_room(_bundle([]), scale=1.0)
    assert room.floor_area_m2 is None
    assert room.wall_area_m2 is None
    assert room.confidence == 0.0


def test_ceiling_not_miscounted_as_floor():
    # Floor at y=0 (area 12), ceiling at y=2.5 (area 12). Floor area must be 12, not summed.
    planes = [
        PlaneAnchor(identifier="floor", alignment="horizontal", classification="floor",
                    transform=I4, center=[0, 0, 0], extent=[3, 4], boundary_vertices=_rect(3, 4)),
        PlaneAnchor(identifier="ceil", alignment="horizontal", classification="ceiling",
                    transform=_translate(2.5), center=[0, 0, 0], extent=[3, 4], boundary_vertices=_rect(3, 4)),
    ]
    room = measure_room(_bundle(planes), scale=1.0)
    assert room.floor_area_m2 == 12.0
