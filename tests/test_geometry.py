"""Synthetic geometry tests — pin the coordinate conventions with known inputs.

These prove *internal consistency* (project/backproject are exact inverses, and
ray-plane / triangulation recover known metric points). Matching real ARKit data
is validated separately against a fiducial marker.
"""

import numpy as np
import pytest

from backend.geometry.camera import (
    backproject,
    plane_from_anchor,
    project,
    ray_plane_intersection,
    rescale_intrinsics,
    triangulate,
)

# A simple pinhole: 1000 px focal, principal point at image centre (640x480).
K = np.array([[1000.0, 0.0, 320.0], [0.0, 1000.0, 240.0], [0.0, 0.0, 1.0]])


def _identity_camera() -> np.ndarray:
    """Camera at origin, looking down -Z (ARKit default orientation)."""
    return np.eye(4)


def _camera_at(x: float, y: float, z: float) -> np.ndarray:
    T = np.eye(4)
    T[:3, 3] = [x, y, z]
    return T


def test_project_backproject_round_trip():
    cam = _camera_at(0.0, 0.0, 0.0)
    # Point 2 m in front of an ARKit camera is at negative Z.
    p = np.array([0.3, -0.2, -2.0])
    uv = project(K, cam, p)
    assert uv is not None
    o, d = backproject(K, cam, *uv)
    # The backprojected ray must pass through p: p = o + t*d for some t>0.
    t = np.dot(p - o, d)
    recovered = o + t * d
    assert np.allclose(recovered, p, atol=1e-6)


def test_point_behind_camera_returns_none():
    cam = _camera_at(0.0, 0.0, 0.0)
    behind = np.array([0.0, 0.0, 2.0])  # +Z is behind an ARKit camera
    assert project(K, cam, behind) is None


def test_ray_plane_intersection_recovers_known_point():
    cam = _camera_at(0.0, 0.0, 0.0)
    # A vertical plane (the shelf front) 1.5 m in front, normal facing +Z (toward cam).
    plane_point = np.array([0.0, 0.0, -1.5])
    plane_normal = np.array([0.0, 0.0, 1.0])
    # Centre pixel should hit the plane straight ahead at (0,0,-1.5).
    o, d = backproject(K, cam, 320.0, 240.0)
    hit = ray_plane_intersection(o, d, plane_point, plane_normal)
    assert hit is not None
    assert np.allclose(hit, [0.0, 0.0, -1.5], atol=1e-6)


def test_spine_height_via_two_pixels_on_plane():
    """A 21 cm spine on a plane 1.5 m away should measure ~21 cm."""
    cam = _camera_at(0.0, 0.0, 0.0)
    plane_point = np.array([0.0, 0.0, -1.5])
    plane_normal = np.array([0.0, 0.0, 1.0])
    # Pick top/bottom world points 0.21 m apart, project to pixels, backproject, intersect.
    top_world = np.array([0.0, 0.105, -1.5])
    bot_world = np.array([0.0, -0.105, -1.5])
    uv_top = project(K, cam, top_world)
    uv_bot = project(K, cam, bot_world)
    ot, dt = backproject(K, cam, *uv_top)
    ob, db = backproject(K, cam, *uv_bot)
    hit_t = ray_plane_intersection(ot, dt, plane_point, plane_normal)
    hit_b = ray_plane_intersection(ob, db, plane_point, plane_normal)
    height = abs(hit_t[1] - hit_b[1])
    assert height == pytest.approx(0.21, abs=1e-4)


def test_triangulate_recovers_point_from_two_views():
    """Thickness path: same 3D point seen from two poses triangulates back."""
    target = np.array([0.4, 0.1, -1.2])
    cam1 = _camera_at(0.0, 0.0, 0.0)
    cam2 = _camera_at(0.25, 0.0, 0.0)  # 25 cm baseline
    o1, d1 = backproject(K, cam1, *project(K, cam1, target))
    o2, d2 = backproject(K, cam2, *project(K, cam2, target))
    point, gap = triangulate(o1, d1, o2, d2)
    assert np.allclose(point, target, atol=1e-5)
    assert gap < 1e-5


def test_rescale_intrinsics():
    K2 = rescale_intrinsics(K, (640, 480), (320, 240))
    assert K2[0, 0] == 500.0 and K2[1, 1] == 500.0
    assert K2[0, 2] == 160.0 and K2[1, 2] == 120.0


def test_plane_from_anchor_normal():
    # Anchor rotated so local +Y maps to world +Z (a vertical wall facing camera).
    anchor = np.eye(4)
    anchor[:3, :3] = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]])  # rot about X
    anchor[:3, 3] = [1.0, 0.0, -2.0]
    point, normal = plane_from_anchor(anchor)
    assert np.allclose(point, [1.0, 0.0, -2.0])
    assert np.allclose(normal, [0.0, 0.0, 1.0], atol=1e-9)
