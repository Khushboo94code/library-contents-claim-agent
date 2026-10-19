# Library Contents Claim Agent

A live voice-and-vision insurance agent that inventories and values a home library
in **one continuous camera sweep**. The policyholder walks the room once while talking
to the agent; the system returns a **contents claim packet**: every book identified from
its spine and priced at local market value, every non-book item identified and priced,
and the room's surface area.

Built for a non-Pro iPhone (no LiDAR): **metric scale comes from ARKit world tracking +
a fiducial marker**, not from a model's guess.

## Architecture (at a glance)

```
iPhone (thin native app)                    Python backend (the brains)
  ARKit world tracking                        ingest    save frames + plane anchors
   ├─ frames + intrinsics + poses    ──►       Stage A  spine detect / count / OCR → edition
   ├─ plane anchors (floor/walls)              Stage B  measurement (planes + raycast + triangulate)
   └─ upload bundle                            Stage C  non-book detect / classify
  WKWebView (next increment)                   Stage D  pricing (reuses scraping repo)
   └─ OpenAI Realtime voice + live UI          assembler  claim_packet.json + report + review queue
                                                          (TOTALS COMPUTED IN CODE, never by an LLM)
```

Each stage is separate and testable; every packet line is traceable to a saved
`frame_ref` and (for prices) a source URL + retrieval date.

## Repo layout

```
backend/
  schema.py            claim_packet.json contract (unknown = null, never a fake 0)
  assembler.py         totals computed in code + review-queue builder
  app.py               FastAPI ingest endpoints
  ingest/              capture bundle schema + on-disk sweep store
  geometry/camera.py   backproject / ray-plane / triangulate (ARKit conventions pinned)
  room/ spines/ items/ pricing/ report/   pipeline stages (in progress)
ios/LibrarySweep/      thin ARKit capture app (Swift) — see ios/README.md
tests/                 contract + geometry + ingest tests
ground_truth/          hand-collected truth vs pass bars (collected before tuning)
```

## Quickstart

### Backend (Python 3.11+)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in OPENAI_API_KEY etc.
pytest -q                       # 13 tests should pass
uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

### iOS capture app

See [`ios/README.md`](ios/README.md) — install Xcode, create the project, drop in the
Swift files, point `BackendConfig.baseURL` at your Mac's LAN IP, run on a physical iPhone.

## Status

| Piece | State |
|-------|-------|
| Output contract + code-computed totals + review queue | ✅ done, tested |
| Capture bundle format (native↔backend) | ✅ done |
| Geometry core (conventions pinned, synthetic tests) | ✅ done, tested |
| Backend ingest endpoints (frame upload + finalize) | ✅ done, tested |
| iOS capture app (ARKit → upload) | ✅ scaffolded (needs on-device run) |
| Stage B: room area + spine measurement + fiducial | ⏳ next |
| Stage A: spine detection / OCR / edition | ⏳ |
| Stage C: non-book detection | ⏳ |
| Stage D: pricing (scraping-repo wrapper) | ⏳ |
| Live voice agent (OpenAI Realtime in WKWebView) | ⏳ |
| Report (HTML/PDF) | ⏳ |

## What we reused (per the brief's "tell us what you reused")

- Existing `scraping` framework (Amazon across US/IN/MX/KSA/UAE/TR/DE/BR + proxy/anti-bot
  infra) for the pricing stage — wrapped in a single-query lookup. See Stage D.

## Key design decisions

- **Metric scale:** ARKit VIO world tracking + plane detection + raycasting, with a
  fiducial marker (known-size ArUco) for global scale correction and convention validation.
- **No hallucination:** prices only from retrievable sources (URL + date); totals computed
  in code from line items; unreadable spines logged as `unidentified` with dimensions,
  never a guessed title.
