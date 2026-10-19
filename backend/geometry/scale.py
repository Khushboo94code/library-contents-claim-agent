"""Compute the metric scale-correction factor from a fiducial marker in the sweep.

Scans sampled frames for the printed ArUco marker, triangulates its corners across two
views using ARKit poses, and compares the measured edge to the known printed size. If no
marker is found, scale defaults to 1.0 (trust ARKit world tracking as-is).
"""

from __future__ import annotations

import numpy as np

from backend.geometry.camera import mat3, mat4
from backend.geometry.fiducial import MarkerDetection, detect_markers, estimate_scale_from_views, load_image
from backend.ingest.capture_schema import CaptureBundle
from backend.ingest.store import SweepStore


def compute_scale(bundle: CaptureBundle, store: SweepStore, sweep_id: str) -> tuple[float, str, float]:
    """Return (scale_factor, scale_method, confidence)."""
    marker_cm = bundle.meta.fiducial_marker_cm
    dict_name = bundle.meta.fiducial_dict or "DICT_4X4_50"
    default = (1.0, "arkit_world_tracking", 0.5)
    if not marker_cm or not bundle.frames:
        return default

    # Sample up to ~12 frames and collect per-marker-id views.
    frames = bundle.frames
    step = max(1, len(frames) // 12)
    by_id: dict[int, list[tuple[np.ndarray, np.ndarray, MarkerDetection]]] = {}
    for fr in frames[::step]:
        path = store.frame_path(sweep_id, fr.image)
        if not path.exists():
            continue
        try:
            img = load_image(str(path))
            dets = detect_markers(img, dict_name)
        except Exception:  # noqa: BLE001
            continue
        if not dets:
            continue
        K = mat3(fr.intrinsics)
        T = mat4(fr.transform)
        for d in dets:
            by_id.setdefault(d.marker_id, []).append((K, T, d))

    # Use the marker seen in the most views.
    if not by_id:
        return default
    best = max(by_id.values(), key=len)
    if len(best) < 2:
        return default

    res = estimate_scale_from_views(best, marker_cm)
    if not res.valid or res.confidence <= 0:
        # Marker found but scale looks wrong (likely a convention bug) — don't apply it.
        return (1.0, "arkit_world_tracking (fiducial rejected)", 0.3)
    return (res.factor, "arkit_world_tracking+fiducial_scale_correction", round(0.6 + 0.4 * res.confidence, 2))
