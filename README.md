# Library Contents Claim Agent

A live voice-and-vision insurance agent that inventories and values a home library in
**one continuous camera sweep**. The policyholder walks the room once while talking to the
agent; the system returns a **contents claim packet**: every book identified from its spine
and priced at local market value, every non-book item identified and priced, and the room's
surface area.

Built for a **non-Pro iPhone (no LiDAR)**: metric scale comes from **ARKit world tracking +
a fiducial marker**, never a model's guess. Prices are **scraped from real Amazon pages**
(URL + date on every figure), never produced by a model. Totals are **computed in code**.

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the pipeline, model-per-stage, where scale
comes from, and where prices come from.

---

## What it does (the sweep)

1. The agent greets you, **confirms country + currency** (device-detected, correctable by
   voice), and explains the sweep in one sentence.
2. You walk the room once, panning across every shelf, wall and floor (target < 3 min).
3. While you walk the agent **talks back**: says what it's logging, tells you to slow down /
   step closer when spines are unreadable, and asks what it can't tell from video
   (*"is that portrait an original or a print?"*). The inventory fills in live on screen.
4. On stop, background stages finish identification, measurement and pricing.
5. Within ~5 min the agent **reads back a summary** and the app shows the
   `claim_packet.json` + a human-readable report + a review queue.

---

## Prerequisites

- **Python 3.11+**
- **Google Cloud project** with Vertex AI enabled, and `gcloud` installed
  (vision + live voice run on Gemini via Vertex, authenticated with ADC — no API key in code).
- **Xcode 15+** and a **physical iPhone** (ARKit; the simulator has no camera/AR).
- *(Optional)* A deployed **GCP Cloud Function** for Amazon fetches — source in
  [`cloud/amazon_fetch/`](cloud/amazon_fetch/). Without it, pricing falls back to a local
  `curl_cffi` + proxy path (set `PROXY_URL`).

---

## Backend — run in under 5 minutes

```bash
# 1. deps
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. auth for Vertex (vision + live voice). One-time.
gcloud auth application-default login

# 3. config
cp .env.example .env            # set GOOGLE_CLOUD_PROJECT, CLOUD_FETCH_URL, etc.

# 4. verify
pytest -q                       # 66 tests should pass (fully offline — network is mocked)

# 5. run (HTTP :8000 for uploads, HTTPS :8443 for the report/web UI)
./run.sh
# or just the HTTP server:
#   uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

`run.sh` prints the Mac's LAN IP — you'll point the iOS app at it.

### Key endpoints

| Endpoint | Purpose |
|---|---|
| `GET  /health` | liveness probe (the app shows a green/red dot) |
| `WS   /live` | live voice bridge (browser/app ↔ Gemini Live) |
| `POST /sweeps/{id}/frame` | upload one captured frame (JPEG + AR metadata) |
| `POST /sweeps/{id}/finalize` | close the sweep; kicks off background processing |
| `GET  /sweeps/{id}/summary` | read-back status + spoken summary once the packet is ready |
| `GET  /sweeps/{id}/report?pdf=1` | human-readable report (HTML, or print-friendly for PDF) |
| `GET  /sweeps/{id}/packet.json` | the structured claim packet |
| `GET  /sweeps` | list all sweeps (claims screen) |

---

## iOS app

```bash
cd ios
# project.yml is the source of truth; regenerate the Xcode project if you have xcodegen:
xcodegen generate          # optional — a committed .xcodeproj is already present
open LibrarySweep.xcodeproj
```

Then, in Xcode:

1. Set the backend host: the Mac's LAN IP (printed by `run.sh`) is referenced in the five
   `ios/LibrarySweep/*.swift` networking files — search-replace it, or see the note below.
2. Pick your development team (Signing & Capabilities) and select a physical iPhone.
3. Build & run. Grant camera + microphone + local-network permissions on first launch.

> **Network note:** the backend host is currently a hardcoded LAN IP in the Swift files
> (`SweepUploader`, `LiveVoiceManager`, `BackendMonitor`, `ClaimsListView`, `ReportView`).
> If your IP changes, update it in those files and rebuild. (A stable `.local` mDNS host is
> a known follow-up.)

---

## Repo layout

```
backend/
  app.py                 FastAPI endpoints (ingest, live WS, report, summary, packet)
  live.py                Gemini Live voice bridge: directing, narration, set_locale, read-back
  vision.py              provider-agnostic vision helper (Gemini on Vertex; OpenAI fallback)
  pipeline.py            orchestrates Stage A–D + assembler (concurrent pricing)
  schema.py              claim_packet.json contract (unknown = null, never a fake 0)
  assembler.py           totals computed in code + review-queue builder
  spines/                Stage A: detect/count/read spines · Stage B: measure (plane + fiducial)
  items/                 Stage C: non-book detection/classification
  pricing/price.py       Stage D: real Amazon pricing per locale (sourced URL + date)
  room/                  floor/wall area from ARKit plane polygons
  geometry/camera.py     backproject / ray-plane / triangulate (ARKit conventions pinned)
  report/render.py       HTML/PDF report + floor-plan SVG
cloud/amazon_fetch/      deployable GCP Cloud Function (India egress IP) for Amazon fetches
ios/LibrarySweep/        thin ARKit capture app + native Gemini Live voice (Swift)
scripts/make_icon.py     renders the app icon from the brand mark
tests/                   66 tests: contract, geometry, ingest, room, pricing, dedup, e2e …
ground_truth/            hand-collected truth vs pass bars (collect before tuning)
```

---

## Guarantees baked in (per the brief)

- **Metric scale is real**, not estimated: ARKit VIO world tracking + plane raycasting, with a
  known-size ArUco fiducial as the scale anchor / fallback. Recorded as `scale_method`.
- **No fabricated prices**: every amount is scraped from a retrievable Amazon page and carries
  its `url` + `retrieved_at`. If nothing is found, the amount is `null` and the line is
  excluded from totals — never invented.
- **No guessed titles**: an unreadable spine is logged as `unidentified` with its dimensions.
- **Totals are computed in code** from the line items, never written by a model.
- **Locale is a setting**: confirmed by voice at the start and used to pick the local Amazon
  marketplace — pricing the same books for a second country is a config change, not code.
- **Honest unknowns**: every low-confidence line goes to the `review_queue` with a reason.

## What we reused

- An existing **`scraping`** framework (Amazon across US/IN/MX/SA/AE/TR/DE/BR with
  proxy/anti-bot infra) informs the pricing path; the deployed GCP Cloud Function gives a
  free rotating India egress IP for `amazon.in` fetches.
- Interaction pattern inspired by the *Insurance Claim Live Agent Team* reference — but where
  the reference *describes* a frame, this agent **measures, counts and prices** it.
