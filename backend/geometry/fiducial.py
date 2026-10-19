"""Fiducial marker (ArUco) — the default metric-scale anchor and convention check.

The measurement analysis recommends a printed marker of known edge length in every
sweep. It does three jobs:

1.  **Absolute scale check** — triangulating the marker's corners across two frames
    (using ARKit poses) gives the marker's edge length *in ARKit units*. Comparing
    to the known printed size yields a global scale-correction factor. If ARKit's
    VIO scale is good, the factor is ~1.0; if it drifted, every metric output is
    multiplied by this factor.
2.  **Convention validation** — if the factor comes out absurd (e.g. 0.3 or 3.0),
    the coordinate-handling is wrong, not ARKit. Catch it before trusting numbers.
3.  **Local px/cm** at the marker's depth for spine-thickness sanity.

Independent of ARKit plane detection — works even when zero planes are found.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from backend.geometry.camera import backproject, mat3, mat4, triangulate

_DICTS = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv2.aruco.DICT_4X4_100,
    "DICT_5X5_50": cv2.aruco.DICT_5X5_50,
    "DICT_6X6_250": cv2.aruco.DICT_6X6_250,
}


def _dictionary(name: str):
    return cv2.aruco.getPredefinedDictionary(_DICTS.get(name, cv2.aruco.DICT_4X4_50))


def generate_marker_png(marker_id: int = 0, dict_name: str = "DICT_4X4_50", px: int = 800) -> bytes:
    """Return PNG bytes of a marker to print. Print at a measured edge length and
    set FIDUCIAL_MARKER_CM to that size."""
    img = cv2.aruco.generateImageMarker(_dictionary(dict_name), marker_id, px)
    # White quiet-zone border improves detection robustness.
    bordered = cv2.copyMakeBorder(img, px // 8, px // 8, px // 8, px // 8, cv2.BORDER_CONSTANT, value=255)
    ok, buf = cv2.imencode(".png", bordered)
    if not ok:
        raise RuntimeError("failed to encode marker PNG")
    return buf.tobytes()


@dataclass
class MarkerDetection:
    marker_id: int
    corners_px: np.ndarray  # (4, 2) image corners, order: TL, TR, BR, BL


def detect_markers(image_bgr: np.ndarray, dict_name: str = "DICT_4X4_50") -> list[MarkerDetection]:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if image_bgr.ndim == 3 else image_bgr
    detector = cv2.aruco.ArucoDetector(_dictionary(dict_name), cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(gray)
    out: list[MarkerDetection] = []
    if ids is None:
        return out
    for c, i in zip(corners, ids.flatten()):
        out.append(MarkerDetection(marker_id=int(i), corners_px=c.reshape(4, 2)))
    return out


@dataclass
class ScaleResult:
    factor: float          # multiply every metric output by this
    measured_edge_m: float  # marker edge length as measured in ARKit units
    known_edge_m: float
    n_views: int
    confidence: float
    valid: bool            # False if the factor is absurd (convention bug suspected)


def estimate_scale_from_views(
    views: list[tuple[np.ndarray, np.ndarray, MarkerDetection]],
    known_edge_cm: float,
) -> ScaleResult:
    """Triangulate the marker's corners across frames and derive the scale factor.

    ``views`` = list of (K 3x3, cam_to_world 4x4, detection) for frames that saw the
    SAME marker id. Needs at least two views with a real baseline.
    """
    known_edge_m = known_edge_cm / 100.0
    if len(views) < 2:
        return ScaleResult(1.0, 0.0, known_edge_m, len(views), 0.0, False)

    # Triangulate each of the 4 corners using the first two views with enough baseline.
    K0, T0, d0 = views[0]
    K1, T1, d1 = views[1]
    corners_world = []
    gaps = []
    for k in range(4):
        o0, dir0 = backproject(K0, T0, float(d0.corners_px[k, 0]), float(d0.corners_px[k, 1]))
        o1, dir1 = backproject(K1, T1, float(d1.corners_px[k, 0]), float(d1.corners_px[k, 1]))
        try:
            p, gap = triangulate(o0, dir0, o1, dir1)
        except ValueError:
            return ScaleResult(1.0, 0.0, known_edge_m, len(views), 0.0, False)
        corners_world.append(p)
        gaps.append(gap)
    corners_world = np.array(corners_world)

    # Marker edge = mean of the four side lengths (TL-TR, TR-BR, BR-BL, BL-TL).
    edges = [np.linalg.norm(corners_world[(k + 1) % 4] - corners_world[k]) for k in range(4)]
    measured_edge_m = float(np.mean(edges))
    if measured_edge_m < 1e-4:
        return ScaleResult(1.0, 0.0, known_edge_m, len(views), 0.0, False)

    factor = known_edge_m / measured_edge_m
    # Confidence: tight triangulation + square marker (edges agree) -> high.
    edge_cv = float(np.std(edges) / np.mean(edges))
    gap_m = float(np.mean(gaps))
    confidence = max(0.0, min(1.0, 1.0 - edge_cv - gap_m))
    valid = 0.5 <= factor <= 2.0  # outside this, suspect a convention bug, not drift
    return ScaleResult(factor, measured_edge_m, known_edge_m, len(views), confidence, valid)


def load_image(path: str) -> np.ndarray:
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(path)
    return img


__all__ = [
    "generate_marker_png",
    "detect_markers",
    "estimate_scale_from_views",
    "MarkerDetection",
    "ScaleResult",
    "load_image",
    "mat3",
    "mat4",
]
