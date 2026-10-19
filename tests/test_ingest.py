"""End-to-end ingest test: upload frames, finalize, read back — no real device needed."""

import json

from fastapi.testclient import TestClient

from backend.app import app, store


def _frame_meta(ts: float) -> str:
    return json.dumps(
        {
            "timestamp": ts,
            "image": "ignored",  # server overwrites with the stored path
            "image_width": 960,
            "image_height": 720,
            "intrinsics": [1000, 0, 480, 0, 1000, 360, 0, 0, 1],
            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -1.5, 0, 0, 0, 1],
            "quality": {"tracking_state": "normal"},
        }
    )


def test_frame_upload_finalize_and_readback(tmp_path, monkeypatch):
    # Point the store at a temp dir so the test leaves no artifacts.
    monkeypatch.setattr(store, "root", tmp_path)
    client = TestClient(app)

    # Upload two frames.
    for ts in (0.100, 0.200):
        r = client.post(
            "/sweeps/test1/frame",
            data={"metadata": _frame_meta(ts)},
            files={"image": ("f.jpg", b"\xff\xd8\xff\xd9fakejpeg", "image/jpeg")},
        )
        assert r.status_code == 200, r.text
        assert r.json()["stored"].startswith("frames/")

    # Finalize with one vertical wall plane.
    plane = {
        "identifier": "p1",
        "alignment": "vertical",
        "classification": "wall",
        "transform": [1, 0, 0, 1, 0, 0, -1, 0, 0, 1, 0, -2, 0, 0, 0, 1],
        "center": [0, 0, 0],
        "extent": [2.0, 2.4],
        "boundary_vertices": [[-1, 0, -1.2], [1, 0, -1.2], [1, 0, 1.2], [-1, 0, 1.2]],
    }
    r = client.post(
        "/sweeps/test1/finalize",
        json={"meta": {"id": "test1", "country": "IN", "currency": "INR"}, "planes": [plane]},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"sweep_id": "test1", "frames": 2, "planes": 1}

    # Read the bundle back; frame_refs must point at saved files.
    bundle = client.get("/sweeps/test1/bundle").json()
    assert len(bundle["frames"]) == 2
    assert bundle["frames"][0]["image"].startswith("frames/")
    rel = bundle["frames"][0]["image"]
    fr = client.get(f"/sweeps/test1/frame/{rel}")
    assert fr.status_code == 200
    assert fr.content.startswith(b"\xff\xd8")  # JPEG magic
