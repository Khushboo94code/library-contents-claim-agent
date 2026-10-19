"""Synthetic spine-measurement test: a known 21 cm x 3 cm spine on a shelf plane
1 m away must measure back to ~21 x ~3 cm."""

import numpy as np
import pytest

from backend.geometry.camera import project
from backend.ingest.capture_schema import CaptureFrame, FrameQuality, PlaneAnchor, SweepMeta
from backend.spines.measure import measure_spine, measure_spine_fiducial

K = np.array([[1000.0, 0, 480], [0, 1000.0, 360], [0, 0, 1]])
CAM = np.eye(4)  # camera at origin, looking down -Z
W, H = 960, 720

# Vertical shelf plane at z=-1 m, normal facing +Z (toward camera).
_PLANE_R = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]  # local +Y -> world +Z
_PLANE_T = [_PLANE_R[0] + [0.0], _PLANE_R[1] + [0.0], _PLANE_R[2] + [-1.0], [0, 0, 0, 1]]
_PLANE_FLAT = [v for row in _PLANE_T for v in row]


def _bbox_for(w_cm: float, h_cm: float):
    """Project a spine of given size (centered, on the plane at z=-1) to a normalized bbox."""
    hw, hh = w_cm / 200.0, h_cm / 200.0  # half-width/height in metres
    top = project(K, CAM, np.array([0, hh, -1.0]))
    bot = project(K, CAM, np.array([0, -hh, -1.0]))
    left = project(K, CAM, np.array([-hw, 0, -1.0]))
    right = project(K, CAM, np.array([hw, 0, -1.0]))
    return [left[0] / W, top[1] / H, right[0] / W, bot[1] / H]


def _frame():
    return CaptureFrame(timestamp=0.1, image="f.jpg", image_width=W, image_height=H,
                        intrinsics=[1000, 0, 480, 0, 1000, 360, 0, 0, 1],
                        transform=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                        quality=FrameQuality(tracking_state="normal"))


def _plane():
    return PlaneAnchor(identifier="shelf", alignment="vertical", classification="wall",
                       transform=_PLANE_FLAT, center=[0, 0, 0], extent=[1, 1],
                       boundary_vertices=[[-0.5, 0, -0.3], [0.5, 0, -0.3], [0.5, 0, 0.3], [-0.5, 0, 0.3]])


def test_spine_dimensions_recovered():
    bbox = _bbox_for(w_cm=3.0, h_cm=21.0)
    hcm, tcm = measure_spine(bbox, _frame(), [_plane()], scale=1.0)
    assert hcm == pytest.approx(21.0, abs=0.5)
    assert tcm == pytest.approx(3.0, abs=0.5)


def test_scale_factor_applied():
    bbox = _bbox_for(w_cm=3.0, h_cm=21.0)
    hcm, tcm = measure_spine(bbox, _frame(), [_plane()], scale=1.1)
    assert hcm == pytest.approx(23.1, abs=0.6)   # 21 * 1.1


def test_no_plane_returns_none():
    bbox = _bbox_for(w_cm=3.0, h_cm=21.0)
    assert measure_spine(bbox, _frame(), [], scale=1.0) == (None, None)


def _cam_bbox_for(w_cm, h_cm):
    """Project a spine onto a camera-space plane at z=+1 m (CV convention)."""
    hw, hh = w_cm / 200.0, h_cm / 200.0
    def px(p): return (K[0, 0] * p[0] / p[2] + K[0, 2], K[1, 1] * p[1] / p[2] + K[1, 2])
    top, bot = px((0, -hh, 1)), px((0, hh, 1))      # CV: +Y is down, so spine top is -Y
    left, right = px((-hw, 0, 1)), px((hw, 0, 1))
    return [left[0] / W, top[1] / H, right[0] / W, bot[1] / H]


def test_fiducial_camera_space_measurement():
    # Marker plane 1 m ahead, facing the camera (normal +Z). Spine 21 x 3 cm on it.
    bbox = _cam_bbox_for(3.0, 21.0)
    hcm, tcm = measure_spine_fiducial(bbox, _frame(),
                                      np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, 1.0]))
    assert hcm == pytest.approx(21.0, abs=0.5)
    assert tcm == pytest.approx(3.0, abs=0.5)
