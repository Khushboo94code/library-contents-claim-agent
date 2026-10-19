"""Tests for the top-down floor-plan SVG generator."""

from __future__ import annotations

from backend.ingest.capture_schema import CaptureBundle, PlaneAnchor, SweepMeta
from backend.report.floorplan import render_floorplan_svg

_IDENTITY = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


def _floor_plane() -> PlaneAnchor:
    # 3 m (X) x 4 m (Z) rectangle on the ground (Y = 0).
    return PlaneAnchor(
        identifier="floor-1",
        alignment="horizontal",
        classification="floor",
        transform=_IDENTITY,
        center=[1.5, 0.0, 2.0],
        extent=[3.0, 4.0],
        boundary_vertices=[[0, 0, 0], [3, 0, 0], [3, 0, 4], [0, 0, 4]],
    )


def _wall_plane() -> PlaneAnchor:
    # A vertical wall running along X at Z = 0, 2.5 m tall. Plane-local +Y is the
    # wall normal; the boundary still lives in plane-local coords. We use a simple
    # transform that places a 3 m x 2.5 m wall in world space along the X axis.
    transform = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,   # map local-Z to world-Y so the wall stands up
        0.0, -1.0, 0.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    return PlaneAnchor(
        identifier="wall-1",
        alignment="vertical",
        classification="wall",
        transform=transform,
        center=[1.5, 0.0, 1.25],
        extent=[3.0, 2.5],
        boundary_vertices=[[0, 0, 0], [3, 0, 0], [3, 0, 2.5], [0, 0, 2.5]],
    )


def test_render_floorplan_svg_with_floor_and_wall():
    bundle = CaptureBundle(
        meta=SweepMeta(id="t1"),
        frames=[],
        planes=[_floor_plane(), _wall_plane()],
    )
    svg = render_floorplan_svg(bundle)

    assert isinstance(svg, str)
    assert "<svg" in svg
    assert "</svg>" in svg
    # The floor footprint must be drawn as a polygon or path.
    assert "<polygon" in svg or "<path" in svg
    # The wall must be drawn as a line segment.
    assert "<line" in svg
    # Scale bar / legend present.
    assert "1 m" in svg
    assert "Floor" in svg
    assert "Wall" in svg


def test_render_floorplan_svg_custom_size():
    bundle = CaptureBundle(planes=[_floor_plane()])
    svg = render_floorplan_svg(bundle, width_px=800, height_px=400)
    assert 'width="800"' in svg
    assert 'height="400"' in svg


def test_empty_bundle_returns_unavailable():
    bundle = CaptureBundle(planes=[])
    svg = render_floorplan_svg(bundle)
    assert "<svg" in svg
    assert "</svg>" in svg
    assert "Floor plan unavailable — no plane data captured." in svg
    # No geometry should be drawn.
    assert "<polygon" not in svg
    assert "<line" not in svg


def test_shelf_plane_distinct_from_wall():
    shelf = PlaneAnchor(
        identifier="shelf-1",
        alignment="vertical",
        classification="table",  # non-wall vertical => shelf/other styling
        transform=_IDENTITY,
        center=[0.0, 0.0, 0.0],
        extent=[1.0, 1.0],
        boundary_vertices=[[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]],
    )
    bundle = CaptureBundle(planes=[_floor_plane(), shelf])
    svg = render_floorplan_svg(bundle)
    assert "Shelf / other" in svg
