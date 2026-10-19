"""Stage B (room) — floor/wall area from ARKit plane anchors.

Design choices (from the measurement analysis):
* Floor area = shoelace area of the floor plane's boundary polygon, unioned across
  fragments with Shapely (summing areas would double-count overlaps).
* Headline wall area = floor-polygon perimeter x ceiling height — robust, because blank
  vertical walls are poorly detected, whereas the floor polygon is reliable.
* All linear results are multiplied by ``scale`` (the fiducial scale-correction factor);
  areas by scale**2.

Returns a Room with None fields when there is nothing to measure (e.g. no planes) — an
empty field is the correct answer when unknown.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from backend.geometry.camera import mat4, plane_from_anchor
from backend.ingest.capture_schema import CaptureBundle, PlaneAnchor
from backend.schema import Room


def _world_boundary(anchor: PlaneAnchor) -> np.ndarray:
    """Boundary vertices (plane-local) -> world coordinates, shape (N, 3)."""
    T = mat4(anchor.transform)
    verts = np.array(anchor.boundary_vertices, dtype=float)
    if verts.size == 0:
        return np.empty((0, 3))
    homog = np.hstack([verts, np.ones((len(verts), 1))])
    return (homog @ T.T)[:, :3]


def _mean_y(a: PlaneAnchor) -> float:
    w = _world_boundary(a)
    return float(np.mean(w[:, 1])) if len(w) else 1e9


def _floor_planes(planes: list[PlaneAnchor]) -> list[PlaneAnchor]:
    """Prefer ARKit-classified floors; else the lowest horizontal planes (not ceilings)."""
    classified = [p for p in planes if p.classification == "floor"]
    if classified:
        return classified
    horiz = [p for p in planes if p.alignment == "horizontal" and p.classification != "ceiling"]
    if not horiz:
        return []
    lowest = min(_mean_y(p) for p in horiz)
    return [p for p in horiz if abs(_mean_y(p) - lowest) < 0.3]


def measure_room(bundle: CaptureBundle, scale: float = 1.0,
                 scale_method: str = "arkit_world_tracking") -> Room:
    planes = bundle.planes
    if not planes:
        return Room(scale_method=scale_method, confidence=0.0)

    floors = _floor_planes(planes)
    walls = [p for p in planes if p.alignment == "vertical"]
    ceilings = [p for p in planes if p.classification == "ceiling"]

    room = Room(scale_method=scale_method)

    # --- Floor polygon(s) -> area + footprint dimensions ---
    floor_polys = []
    floor_y = None
    for f in floors:
        w = _world_boundary(f)
        if len(w) < 3:
            continue
        floor_polys.append(Polygon(w[:, [0, 2]]))  # project to X-Z ground plane
        fy = float(np.mean(w[:, 1]))
        floor_y = fy if floor_y is None else min(floor_y, fy)

    merged = None
    if floor_polys:
        merged = unary_union(floor_polys)
        room.floor_area_m2 = round(merged.area * scale * scale, 3)
        minx, miny, maxx, maxy = merged.bounds
        room.length_m = round(max(maxx - minx, maxy - miny) * scale, 3)
        room.width_m = round(min(maxx - minx, maxy - miny) * scale, 3)

    # --- Ceiling height ---
    height = None
    if ceilings and floor_y is not None:
        cy = float(np.mean([np.mean(_world_boundary(c)[:, 1]) for c in ceilings if len(_world_boundary(c))]))
        height = cy - floor_y
    if height is None and walls:
        # fall back to the tallest wall's vertical extent (90th percentile, robust)
        extents = []
        for wll in walls:
            w = _world_boundary(wll)
            if len(w):
                extents.append(float(w[:, 1].max() - w[:, 1].min()))
        if extents:
            height = float(np.percentile(extents, 90))
    if height:
        room.height_m = round(height * scale, 3)

    # --- Wall area = floor perimeter x ceiling height (robust headline) ---
    if merged is not None and height:
        perimeter = merged.exterior.length
        room.wall_area_m2 = round(perimeter * height * scale * scale, 3)

    # --- Confidence: do we have a floor and a height? ---
    conf = 0.0
    if room.floor_area_m2:
        conf += 0.6
    if room.height_m:
        conf += 0.3
    if len(planes) >= 4:
        conf += 0.1
    room.confidence = round(min(conf, 1.0), 2)
    return room
