"""Camera geometry: backprojection, ray-plane intersection, triangulation.

This module is the single highest silent-bug risk in the project (ranked R6 in
the measurement design): ARKit's coordinate conventions differ from the textbook
pinhole model, and a wrong sign produces *plausible-but-wrong* metric numbers
that pass a smell test. Two defences:

1.  ``project`` (world->pixel) and ``backproject`` (pixel->world ray) are exact
    inverses under one documented convention. ``test_geometry.py`` round-trips
    them, so internal consistency is pinned.
2.  Internal consistency is necessary but not sufficient — the convention must
    also match *real ARKit data*. That is validated separately against a
    fiducial marker of known size (see backend/geometry/fiducial.py). Do not
    trust any room/spine number until the fiducial calibration test passes.

Convention used here
--------------------
* Matrices arrive row-major (16 floats) and are reshaped to 4x4.
* ARKit ``camera.transform`` is camera-to-world. ARKit camera space is
  +X right, +Y up, **-Z forward** (the camera looks down -Z).
* The pinhole intrinsics K assume the standard CV camera: +X right, +Y down,
  **+Z forward**. So converting a CV camera-space ray to ARKit camera space
  flips Y and Z:  (x, y, z)_cv -> (x, -y, -z)_arkit.
"""

from __future__ import annotations

import numpy as np

# CV-camera -> ARKit-camera axis flip (diagonal: X keep, Y flip, Z flip).
_CV_TO_ARKIT = np.diag([1.0, -1.0, -1.0])


def mat4(flat: list[float]) -> np.ndarray:
    """Row-major 16-float list -> 4x4 matrix."""
    a = np.asarray(flat, dtype=float)
    if a.size != 16:
        raise ValueError(f"expected 16 floats for a 4x4 matrix, got {a.size}")
    return a.reshape(4, 4)


def mat3(flat: list[float]) -> np.ndarray:
    a = np.asarray(flat, dtype=float)
    if a.size != 9:
        raise ValueError(f"expected 9 floats for a 3x3 matrix, got {a.size}")
    return a.reshape(3, 3)


def rescale_intrinsics(K: np.ndarray, from_wh: tuple[int, int], to_wh: tuple[int, int]) -> np.ndarray:
    """Rescale K when the image is resized before upload.

    Classic silent bug: detections run on a downscaled frame but K is for the
    full-res capture. Always rescale together.
    """
    sx = to_wh[0] / from_wh[0]
    sy = to_wh[1] / from_wh[1]
    K2 = K.copy()
    K2[0, 0] *= sx  # fx
    K2[0, 2] *= sx  # cx
    K2[1, 1] *= sy  # fy
    K2[1, 2] *= sy  # cy
    return K2


def camera_position(cam_to_world: np.ndarray) -> np.ndarray:
    """World-space position of the camera (translation column)."""
    return cam_to_world[:3, 3].copy()


def backproject(K: np.ndarray, cam_to_world: np.ndarray, u: float, v: float) -> tuple[np.ndarray, np.ndarray]:
    """Pixel (u, v) -> (ray_origin_world, ray_dir_world), dir normalized.

    The ray starts at the camera centre and passes through the pixel, pointing
    into the scene (down ARKit's -Z, then rotated into world).
    """
    Kinv = np.linalg.inv(K)
    dir_cv = Kinv @ np.array([u, v, 1.0])  # ray in CV camera space (+Z forward)
    dir_arkit = _CV_TO_ARKIT @ dir_cv  # into ARKit camera space (-Z forward)
    R = cam_to_world[:3, :3]
    dir_world = R @ dir_arkit
    dir_world = dir_world / np.linalg.norm(dir_world)
    return camera_position(cam_to_world), dir_world


def project(K: np.ndarray, cam_to_world: np.ndarray, p_world: np.ndarray) -> tuple[float, float] | None:
    """World point -> pixel (u, v). Inverse of :func:`backproject`.

    Returns None if the point is behind the camera. Used only for testing /
    reprojection checks, not in the measurement path.
    """
    world_to_cam = np.linalg.inv(cam_to_world)
    p_cam_arkit = (world_to_cam @ np.append(p_world, 1.0))[:3]
    p_cam_cv = _CV_TO_ARKIT @ p_cam_arkit  # back to CV convention (+Z forward)
    if p_cam_cv[2] <= 1e-9:
        return None  # behind the camera
    px = K @ p_cam_cv
    return float(px[0] / px[2]), float(px[1] / px[2])


def plane_from_anchor(anchor_to_world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """ARPlaneAnchor transform -> (point_on_plane_world, unit_normal_world).

    ARKit plane-local normal is +Y; the plane lies in local XZ.
    """
    point = anchor_to_world[:3, 3].copy()
    normal = anchor_to_world[:3, :3] @ np.array([0.0, 1.0, 0.0])
    normal = normal / np.linalg.norm(normal)
    return point, normal


def ray_plane_intersection(
    origin: np.ndarray,
    direction: np.ndarray,
    plane_point: np.ndarray,
    plane_normal: np.ndarray,
) -> np.ndarray | None:
    """Intersect a ray with an infinite plane. None if parallel / behind."""
    denom = float(np.dot(direction, plane_normal))
    if abs(denom) < 1e-9:
        return None  # ray parallel to plane
    t = float(np.dot(plane_point - origin, plane_normal)) / denom
    if t <= 0:
        return None  # intersection behind the ray origin
    return origin + t * direction


def triangulate(
    o1: np.ndarray, d1: np.ndarray, o2: np.ndarray, d2: np.ndarray
) -> tuple[np.ndarray, float]:
    """Midpoint of the closest approach of two world-space rays.

    This is how spine *thickness* is measured without dense depth: the same edge
    seen from two poses triangulates to a metric 3D point, with no reliance on a
    (wrong) planarity assumption. Returns (point, gap) where ``gap`` is the
    distance between the rays at closest approach — a built-in quality signal.
    """
    d1 = d1 / np.linalg.norm(d1)
    d2 = d2 / np.linalg.norm(d2)
    r = o1 - o2
    a = float(np.dot(d1, d1))
    b = float(np.dot(d1, d2))
    c = float(np.dot(d2, d2))
    d = float(np.dot(d1, r))
    e = float(np.dot(d2, r))
    denom = a * c - b * b
    if abs(denom) < 1e-9:
        # Rays nearly parallel (tiny baseline) — triangulation is ill-posed.
        raise ValueError("degenerate triangulation: baseline too small")
    t1 = (b * e - c * d) / denom
    t2 = (a * e - b * d) / denom
    p1 = o1 + t1 * d1
    p2 = o2 + t2 * d2
    gap = float(np.linalg.norm(p1 - p2))
    return (p1 + p2) / 2.0, gap
