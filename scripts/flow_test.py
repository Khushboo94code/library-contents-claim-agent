"""End-to-end app-flow test: drives every backend interaction the iOS app makes.

Steps (mirrors the app): health -> live voice (WS: ready/greeting/tool-call/tool-result +
observation sync) -> upload real frames -> finalize -> run pipeline (Gemini vision + pricing)
-> fetch report. Uses real captured frames so Gemini actually detects items.
"""

import asyncio
import glob
import json
import os
import ssl

import httpx
import websockets

HTTP = "http://192.168.1.3:8000"
WS = "ws://192.168.1.3:8000/live"
SID = "flowtest1"


def log(step, msg): print(f"[{step}] {msg}")


def pick_real_frames(n=3):
    dirs = [d for d in glob.glob("data/sweeps/sweep-*") if glob.glob(d + "/frames/*.jpg")]
    best = max(dirs, key=lambda d: len(glob.glob(d + "/frames/*.jpg")))
    frames = sorted(glob.glob(best + "/frames/*.jpg"))
    step = max(1, len(frames) // n)
    return [frames[i * step] for i in range(n)]


async def test_live_ws():
    audio = 0; transcript = ""; tool_calls = []
    async with websockets.connect(WS, max_size=None) as ws:
        # wait for ready
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            if m.get("type") == "ready":
                break
        log("LIVE", "got ready ✓")

        async def one_turn(text, timeout=25):
            """Send one turn and drain until turn_complete (mirrors the app's PTT: one at a time)."""
            nonlocal audio, transcript
            await ws.send(json.dumps({"type": "text", "text": text}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                if isinstance(raw, (bytes, bytearray)):
                    audio += len(raw); continue
                ev = json.loads(raw)
                if ev.get("type") == "out_transcript":
                    transcript += ev.get("text", "")
                elif ev.get("type") == "tool_call":
                    tool_calls.append(ev)
                    await ws.send(json.dumps({"type": "tool_result", "id": ev.get("id"), "name": ev["name"], "output": "logged"}))
                elif ev.get("type") == "turn_complete":
                    return

        await one_turn("Greet me in one short sentence.")
        await one_turn("You see a shelf of orange Penguin paperbacks. Call log_observation for it.")
    log("LIVE", f"audio={audio}B transcript={transcript[:60]!r} tool_calls={[t['name'] for t in tool_calls]}")
    assert audio > 0, "no agent audio"
    assert tool_calls, "agent never called the tool"


def test_rest_flow():
    frames = pick_real_frames(3)
    log("CAPTURE", f"using {len(frames)} real frames")
    with httpx.Client(timeout=30) as c:
        # frames
        for i, fp in enumerate(frames):
            meta = json.dumps({"timestamp": float(i) / 3, "image": "x", "image_width": 960, "image_height": 720,
                               "intrinsics": [722, 0, 484, 0, 722, 360, 0, 0, 1],
                               "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -1.5, 0, 0, 0, 1],
                               "quality": {"tracking_state": "normal"}})
            r = c.post(f"{HTTP}/sweeps/{SID}/frame", data={"metadata": meta},
                       files={"image": ("f.jpg", open(fp, "rb").read(), "image/jpeg")})
            assert r.status_code == 200, r.text
        log("UPLOAD", f"{len(frames)} frames uploaded ✓")
        # observation (live sync)
        r = c.post(f"{HTTP}/sweeps/{SID}/observation",
                   json={"kind": "shelf", "label": "orange Penguin paperbacks", "note": "", "frame_ref": "", "at": 1.0})
        assert r.status_code == 200; log("OBSERVE", f"observation synced ✓ (count={r.json()['count']})")
        # finalize with a floor plane so the room measures
        floor = {"identifier": "floor", "alignment": "horizontal", "classification": "floor",
                 "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "center": [0, 0, 0],
                 "extent": [3, 4], "boundary_vertices": [[0, 0, 0], [3, 0, 0], [3, 0, 4], [0, 0, 4]]}
        r = c.post(f"{HTTP}/sweeps/{SID}/finalize",
                   json={"meta": {"id": SID, "country": "IN", "currency": "INR"}, "planes": [floor]})
        assert r.status_code == 200; log("FINALIZE", f"{r.json()} ✓")
        # run pipeline synchronously (Gemini vision + pricing)
        r = c.post(f"{HTTP}/sweeps/{SID}/process", timeout=300)
        assert r.status_code == 200, r.text
        log("PIPELINE", f"{r.json()} ✓")
        # report
        r = c.get(f"{HTTP}/sweeps/{SID}/report")
        assert r.status_code == 200 and "Contents Claim Packet" in r.text
        log("REPORT", f"rendered {len(r.text)} chars ✓")
    # inspect packet
    p = json.load(open(f"data/sweeps/{SID}/packet.json"))
    log("PACKET", f"items={len(p['items'])} room_floor={p['room']['floor_area_m2']} "
                  f"evidence={len(p['evidence'])} observations={len(p['agent_observations'])} "
                  f"review_queue={len(p['review_queue'])}")
    assert p["room"]["floor_area_m2"] == 12.0, "room not measured"
    assert len(p["agent_observations"]) >= 1, "observation not in packet"


async def main():
    r = httpx.get(f"{HTTP}/health", timeout=10); assert r.json()["ok"]; log("HEALTH", "ok ✓")
    await test_live_ws()
    test_rest_flow()
    print("\n✅ FULL APP FLOW PASSED")


if __name__ == "__main__":
    asyncio.run(main())
