"""Top-down floor-plan SVG generator for the insurance claim report.

Draws a simple top-down (plan) sketch of a room from ARKit plane anchors:

* the FLOOR polygon(s) give the room footprint (projected to the world X-Z plane),
* VERTICAL planes become thick line segments (a wall seen from above is a line),
* non-wall vertical planes (shelves, etc.) are drawn in a distinct colour.

This is deliberately self-contained — pure Python string building, numpy only for
the plane->world transform. No matplotlib, no external SVG library. The output is a
standalone ``<svg>...</svg>`` string ready to embed in an HTML report.

Coordinate note: we look straight down, so "up" in the drawing is -Z and "right" is
+X. The SVG y-axis grows downward, which we use to flip Z into screen space.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from backend.geometry.camera import mat4
from backend.ingest.capture_schema import CaptureBundle, PlaneAnchor
from backend.schema import Room

# --- styling (kept in one place so the look is easy to tweak) ---
_FLOOR_FILL = "#eef3f8"
_FLOOR_STROKE = "#5b7fa6"
_WALL_STROKE = "#2d3a47"
_SHELF_STROKE = "#c0742b"
_SCALE_STROKE = "#2d3a47"
_TEXT_FILL = "#2d3a47"
_BG = "#ffffff"
_PAD_FRAC = 0.08  # fraction of the longer world span added as margin
_WALL_WIDTH_PX = 5.0
_SHELF_WIDTH_PX = 4.0

_UNAVAILABLE_MSG = "Floor plan unavailable — no plane data captured."


def _world_boundary(anchor: PlaneAnchor) -> np.ndarray:
    """Plane-local ``boundary_vertices`` -> world coordinates, shape (N, 3)."""
    verts = np.array(anchor.boundary_vertices, dtype=float)
    if verts.size == 0:
        return np.empty((0, 3))
    T = mat4(anchor.transform)
    homog = np.hstack([verts, np.ones((len(verts), 1))])
    return (homog @ T.T)[:, :3]


def _is_floor(p: PlaneAnchor) -> bool:
    if p.classification == "floor":
        return True
    # No ARKit "floor" label: treat a horizontal plane that isn't a ceiling as floor.
    return p.alignment == "horizontal" and p.classification != "ceiling"


def _xz(world: np.ndarray) -> np.ndarray:
    """Project world Nx3 to the X-Z ground plane (Nx2: columns [X, Z])."""
    return world[:, [0, 2]]


def _unavailable_svg(width_px: int, height_px: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_px}" '
        f'height="{height_px}" viewBox="0 0 {width_px} {height_px}">'
        f'<rect x="0" y="0" width="{width_px}" height="{height_px}" fill="{_BG}"/>'
        f'<text x="{width_px / 2:.1f}" y="{height_px / 2:.1f}" '
        f'text-anchor="middle" dominant-baseline="middle" '
        f'font-family="sans-serif" font-size="14" fill="{_TEXT_FILL}">'
        f"{_UNAVAILABLE_MSG}</text></svg>"
    )


def render_floorplan_svg(
    bundle: CaptureBundle,
    room: Optional[Room] = None,
    width_px: int = 600,
    height_px: int = 600,
) -> str:
    """Render a top-down floor-plan SVG (world X-Z view) as a string.

    Args:
        bundle: the uploaded capture bundle; ``bundle.planes`` drives the drawing.
        room: optional measured Room (unused for geometry, accepted for API symmetry
            so callers can pass the measurement result alongside the bundle).
        width_px, height_px: pixel size of the output SVG.

    Returns a standalone ``<svg>...</svg>`` string. If there are no usable planes it
    returns a small placeholder SVG stating the floor plan is unavailable (no crash).
    """
    planes = list(bundle.planes) if bundle and bundle.planes else []

    # Gather geometry projected to X-Z.
    floor_polys: list[np.ndarray] = []
    wall_segs: list[np.ndarray] = []  # vertical classification == "wall"
    shelf_segs: list[np.ndarray] = []  # vertical, other classifications

    for p in planes:
        world = _world_boundary(p)
        if len(world) == 0:
            continue
        pts = _xz(world)
        if _is_floor(p):
            if len(pts) >= 3:
                floor_polys.append(pts)
        elif p.alignment == "vertical":
            if len(pts) >= 2:
                # A wall seen top-down is a line: use the two extreme points along
                # its dominant horizontal direction (its X-Z extent).
                seg = _dominant_segment(pts)
                if p.classification == "wall" or p.classification == "":
                    wall_segs.append(seg)
                else:
                    shelf_segs.append(seg)

    if not floor_polys and not wall_segs and not shelf_segs:
        return _unavailable_svg(width_px, height_px)

    # --- bounding box over all geometry (world X-Z) ---
    all_pts = [p for poly in floor_polys for p in poly]
    all_pts += [p for seg in wall_segs for p in seg]
    all_pts += [p for seg in shelf_segs for p in seg]
    arr = np.array(all_pts, dtype=float)
    min_x, min_z = arr[:, 0].min(), arr[:, 1].min()
    max_x, max_z = arr[:, 0].max(), arr[:, 1].max()

    span_x = max(max_x - min_x, 1e-6)
    span_z = max(max_z - min_z, 1e-6)
    pad = _PAD_FRAC * max(span_x, span_z)
    min_x -= pad
    min_z -= pad
    max_x += pad
    max_z += pad
    span_x = max_x - min_x
    span_z = max_z - min_z

    # --- uniform scale (metres -> px) keeping aspect ratio, centred ---
    sx = width_px / span_x
    sz = height_px / span_z
    scale = min(sx, sz)
    draw_w = span_x * scale
    draw_h = span_z * scale
    off_x = (width_px - draw_w) / 2.0
    off_y = (height_px - draw_h) / 2.0

    def to_px(x: float, z: float) -> tuple[float, float]:
        px = off_x + (x - min_x) * scale
        # flip Z so smaller Z (further "up") is near the top of the SVG.
        py = off_y + (max_z - z) * scale
        return px, py

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_px}" '
        f'height="{height_px}" viewBox="0 0 {width_px} {height_px}">'
    )
    parts.append(
        f'<rect x="0" y="0" width="{width_px}" height="{height_px}" fill="{_BG}"/>'
    )

    # Floor polygons.
    for poly in floor_polys:
        pts_str = " ".join(
            f"{x:.1f},{y:.1f}" for x, y in (to_px(px, pz) for px, pz in poly)
        )
        parts.append(
            f'<polygon points="{pts_str}" fill="{_FLOOR_FILL}" '
            f'stroke="{_FLOOR_STROKE}" stroke-width="2" '
            f'stroke-linejoin="round"/>'
        )

    # Walls.
    for seg in wall_segs:
        (x1, y1), (x2, y2) = to_px(*seg[0]), to_px(*seg[1])
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{_WALL_STROKE}" stroke-width="{_WALL_WIDTH_PX}" '
            f'stroke-linecap="round"/>'
        )

    # Shelves / other vertical planes.
    for seg in shelf_segs:
        (x1, y1), (x2, y2) = to_px(*seg[0]), to_px(*seg[1])
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{_SHELF_STROKE}" stroke-width="{_SHELF_WIDTH_PX}" '
            f'stroke-linecap="round"/>'
        )

    parts.append(_scale_bar_svg(scale, width_px, height_px))
    parts.append(_legend_svg(bool(shelf_segs)))

    parts.append("</svg>")
    return "".join(parts)


def _dominant_segment(pts: np.ndarray) -> np.ndarray:
    """Collapse a projected polygon to the line segment spanning its widest extent.

    A vertical plane projects to (roughly) a line in X-Z. We pick the pair of points
    with the maximum separation, which captures the wall's horizontal run.
    """
    if len(pts) == 2:
        return pts
    # Compare the X-span vs Z-span to choose the axis along which to take extremes;
    # fall back to the farthest-apart pair for diagonal walls.
    centered = pts - pts.mean(axis=0)
    # principal direction via SVD (most robust for a near-collinear set).
    try:
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        direction = vt[0]
    except np.linalg.LinAlgError:  # pragma: no cover - degenerate input
        direction = np.array([1.0, 0.0])
    proj = centered @ direction
    lo = pts[int(np.argmin(proj))]
    hi = pts[int(np.argmax(proj))]
    return np.array([lo, hi])


def _scale_bar_svg(scale: float, width_px: int, height_px: int) -> str:
    """A 1 m reference bar in the bottom-left corner."""
    bar_px = 1.0 * scale  # 1 metre in px
    margin = 16.0
    y = height_px - margin
    x0 = margin
    x1 = margin + bar_px
    tick = 4.0
    return (
        f'<g stroke="{_SCALE_STROKE}" stroke-width="2" fill="none">'
        f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" y2="{y:.1f}"/>'
        f'<line x1="{x0:.1f}" y1="{y - tick:.1f}" x2="{x0:.1f}" y2="{y + tick:.1f}"/>'
        f'<line x1="{x1:.1f}" y1="{y - tick:.1f}" x2="{x1:.1f}" y2="{y + tick:.1f}"/>'
        f"</g>"
        f'<text x="{(x0 + x1) / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="11" fill="{_TEXT_FILL}">1 m</text>'
    )


def _legend_svg(has_shelf: bool) -> str:
    """A small floor / wall (/ shelf) legend in the top-left corner."""
    x = 16.0
    y = 20.0
    dy = 18.0
    rows = [
        (_FLOOR_FILL, _FLOOR_STROKE, "Floor"),
        (_WALL_STROKE, _WALL_STROKE, "Wall"),
    ]
    if has_shelf:
        rows.append((_SHELF_STROKE, _SHELF_STROKE, "Shelf / other"))

    out: list[str] = ["<g font-family=\"sans-serif\" font-size=\"11\">"]
    for i, (fill, stroke, label) in enumerate(rows):
        ry = y + i * dy
        out.append(
            f'<rect x="{x:.1f}" y="{ry - 9:.1f}" width="14" height="12" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
        )
        out.append(
            f'<text x="{x + 20:.1f}" y="{ry:.1f}" fill="{_TEXT_FILL}">{label}</text>'
        )
    out.append("</g>")
    return "".join(out)
