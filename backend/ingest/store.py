"""On-disk storage for uploaded sweeps.

Layout (one directory per sweep)::

    data/sweeps/<sweep_id>/
        frames/<timestamp>.jpg      # every saved frame; its path is the frame_ref
        bundle.json                 # CaptureBundle: meta + planes + frame index
        packet.json                 # written later by the pipeline

Frames are uploaded incrementally during the sweep (so the UI can fill live);
``finalize`` writes the bundle that references them. Nothing here fabricates
data — a frame_ref always points to a file that exists.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.ingest.capture_schema import CaptureBundle, CaptureFrame, PlaneAnchor, SweepMeta


class SweepStore:
    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, sweep_id: str) -> Path:
        d = self.root / sweep_id
        (d / "frames").mkdir(parents=True, exist_ok=True)
        return d

    def save_frame(self, sweep_id: str, frame: CaptureFrame, image_bytes: bytes) -> str:
        """Persist a frame's JPEG and return its stored relative path (frame_ref)."""
        d = self._dir(sweep_id)
        rel = f"frames/{frame.timestamp:.3f}.jpg"
        (d / rel).write_bytes(image_bytes)
        # Rewrite the image field to the stored path so it is a real frame_ref.
        frame = frame.model_copy(update={"image": rel})
        self._append_frame_index(sweep_id, frame)
        return rel

    def _index_path(self, sweep_id: str) -> Path:
        return self._dir(sweep_id) / "frames_index.jsonl"

    def _append_frame_index(self, sweep_id: str, frame: CaptureFrame) -> None:
        with self._index_path(sweep_id).open("a") as f:
            f.write(frame.model_dump_json() + "\n")

    def load_frames(self, sweep_id: str) -> list[CaptureFrame]:
        p = self._index_path(sweep_id)
        if not p.exists():
            return []
        return [CaptureFrame.model_validate_json(line) for line in p.read_text().splitlines() if line.strip()]

    def finalize(self, sweep_id: str, meta: SweepMeta, planes: list[PlaneAnchor]) -> CaptureBundle:
        """Write bundle.json referencing all uploaded frames + final plane anchors."""
        bundle = CaptureBundle(meta=meta, frames=self.load_frames(sweep_id), planes=planes)
        (self._dir(sweep_id) / "bundle.json").write_text(bundle.model_dump_json(indent=2))
        return bundle

    def load_bundle(self, sweep_id: str) -> CaptureBundle:
        p = self._dir(sweep_id) / "bundle.json"
        return CaptureBundle.model_validate_json(p.read_text())

    def _obs_path(self, sweep_id: str) -> Path:
        return self._dir(sweep_id) / "observations.jsonl"

    def append_observation(self, sweep_id: str, obs: dict) -> None:
        """Live packet sync: persist one agent observation during the sweep."""
        with self._obs_path(sweep_id).open("a") as f:
            f.write(json.dumps(obs) + "\n")

    def load_observations(self, sweep_id: str) -> list[dict]:
        p = self._obs_path(sweep_id)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def packet_path(self, sweep_id: str) -> Path:
        return self._dir(sweep_id) / "packet.json"

    def write_packet(self, sweep_id: str, packet_json: str) -> Path:
        p = self.packet_path(sweep_id)
        p.write_text(packet_json)
        return p

    def frame_path(self, sweep_id: str, rel: str) -> Path:
        return self._dir(sweep_id) / rel
