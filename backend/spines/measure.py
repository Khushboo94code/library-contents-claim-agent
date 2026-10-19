"""Stage B (spines) — height & thickness in cm for a detected spine.

Two metric references, tried in order:

1.  ARKit vertical plane (shelf front) — raycast the spine's bbox corners in WORLD space.
2.  Fiducial marker (known printed size) — when there's no ARKit plane, solvePnP on the
    marker gives a metric plane in CAMERA space, and we raycast the bbox corners onto it.
    This is the "reference object" scale the brief allows, and the only path that works when
    ARKit detects no planes.

Both compute the spine face (height x thickness) from the 3D hit points. Returns (None, None)
when neither reference is available — honest rather than guessed.
"""

from __future__ import annotations

import numpy as np

from backend.geometry.camera import backproject, mat3, mat4, plane_from_anchor, ray_plane_intersection
from backend.ingest.capture_schema import CaptureFrame, PlaneAnchor

# Plausible spine bounds (cm) — discard absurd reads from a bad plane rather than emit garbage.
_H_MIN, _H_MAX = 2.0, 45.0
_T_MIN, _T_MAX = 0.3, 12.0


def _dims_from_corners(top, bot, left, right, scale: float) -> tuple[float | None, float | None]:
    if any(v is None for v in (top, bot, left, right)):
        return (None, None)
    hcm = round(float(np.linalg.norm(top - bot)) * 100 * scale, 1)      # spine height
    tcm = round(float(np.linalg.norm(right - left)) * 100 * scale, 1)   # spine thickness
    if not (_H_MIN <= hcm <= _H_MAX) or not (_T_MIN <= tcm <= _T_MAX):
        return (None, None)
    return (hcm, tcm)


# --- Path 1: ARKit vertical plane (world space) ----------------------------------------

def measure_spine(bbox: list[float], frame: CaptureFrame, planes: list[PlaneAnchor],
                  scale: float = 1.0) -> tuple[float | None, float | None]:
    """bbox = [x0,y0,x1,y1] normalized (0..1), top-left origin."""
    verts = [p for p in planes if p.alignment == "vertical"]
    if not verts or not bbox or len(bbox) != 4:
        return (None, None)
    K, T = mat3(frame.intrinsics), mat4(frame.transform)
    w, h = frame.image_width, frame.image_height
    x0, y0, x1, y1 = bbox

    o, d = backproject(K, T, (x0 + x1) / 2 * w, (y0 + y1) / 2 * h)
    best, best_t = None, None
    for p in verts:
        pt, nrm = plane_from_anchor(mat4(p.transform))
        hit = ray_plane_intersection(o, d, pt, nrm)
        if hit is None:
            continue
        t = float(np.linalg.norm(hit - o))
        if best_t is None or t < best_t:
            best_t, best = t, (pt, nrm)
    if best is None:
        return (None, None)
    pt, nrm = best

    def hw(px, py):
        ro, rd = backproject(K, T, px * w, py * h)
        return ray_plane_intersection(ro, rd, pt, nrm)

    return _dims_from_corners(hw((x0 + x1) / 2, y0), hw((x0 + x1) / 2, y1),
                              hw(x0, (y0 + y1) / 2), hw(x1, (y0 + y1) / 2), scale)


# --- Path 2: fiducial marker (camera space) --------------------------------------------

def fiducial_plane_camera(image_bgr, K: np.ndarray, marker_cm: float,
                          dict_name: str = "DICT_4X4_50"):
    """Detect the marker and solvePnP -> (point, normal) of the marker plane in CAMERA
    coordinates (metres). Returns None if no marker or the pose solve fails."""
    try:
        import cv2
        from backend.geometry.fiducial import detect_markers
    except Exception:
        return None
    dets = detect_markers(image_bgr, dict_name)
    if not dets:
        return None
    s = (marker_cm / 100.0) / 2.0
    # Object points in the marker's own frame, matching ArUco corner order (TL,TR,BR,BL).
    objp = np.array([[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]], dtype=np.float32)
    ok, rvec, tvec = cv2.solvePnP(objp, dets[0].corners_px.astype(np.float32),
                                  K.astype(np.float32), None)
    if not ok:
        return None
    R, _ = cv2.Rodrigues(rvec)
    return tvec.reshape(3), (R @ np.array([0.0, 0.0, 1.0]))


def measure_spine_fiducial(bbox: list[float], frame: CaptureFrame,
                           plane_pt_cam: np.ndarray, plane_n_cam: np.ndarray,
                           scale: float = 1.0) -> tuple[float | None, float | None]:
    """Raycast the spine bbox corners onto the marker plane, in CAMERA space."""
    if not bbox or len(bbox) != 4:
        return (None, None)
    K = mat3(frame.intrinsics)
    Kinv = np.linalg.inv(K)
    w, h = frame.image_width, frame.image_height
    x0, y0, x1, y1 = bbox
    origin = np.zeros(3)

    def hit(px, py):
        d = Kinv @ np.array([px * w, py * h, 1.0])   # camera-space ray (CV: +Z forward)
        d = d / np.linalg.norm(d)
        return ray_plane_intersection(origin, d, plane_pt_cam, plane_n_cam)

    return _dims_from_corners(hit((x0 + x1) / 2, y0), hit((x0 + x1) / 2, y1),
                              hit(x0, (y0 + y1) / 2), hit(x1, (y0 + y1) / 2), scale)
