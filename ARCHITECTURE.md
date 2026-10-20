# Architecture

One live sweep in, one priced `claim_packet.json` + report out. A thin native iOS capture
layer feeds a Python backend of **separable, testable stages**; a live voice agent directs the
capture in real time. Nothing that becomes a number is produced by a language model — scale
comes from ARKit geometry, prices from scraped pages, and totals from code.

## System diagram

```
┌─────────────────────────── iPhone (thin native app, ARKit) ───────────────────────────┐
│  ARWorldTrackingConfiguration (VIO, no LiDAR)                                           │
│   ├─ RGB frames + camera intrinsics + world poses ──┐                                   │
│   ├─ ARPlaneAnchors (floor / walls / shelf fronts) ─┤  POST /sweeps/{id}/frame          │
│   ├─ on-device capture cues (tracking state) ───────┘  POST /sweeps/{id}/finalize       │
│   └─ native Gemini Live voice (push-to-talk)  ⇄  WS /live                               │
└───────────────────────────────────────────────┬────────────────────────────────────────┘
                                                 │
┌──────────────────────────── Python backend (FastAPI) ───────────────────────────────────┐
│                                                                                          │
│  LIVE (during sweep)                         BACKGROUND (after finalize, < 5 min)         │
│  live.py  Gemini Live bridge                 pipeline.py orchestrates:                     │
│   • directs: "slow down / step closer"        Stage A  spines → detect · count · OCR       │
│   • narrates what it's logging                Stage B  measurement (planes + fiducial)      │
│   • asks "original or print?"                 Stage C  non-book items → detect · classify   │
│   • set_locale → country/currency             Stage D  pricing (real Amazon pages)          │
│   • log_observation → live inventory          room/    floor + wall area from plane polygons│
│   • reads back the summary at the end         assembler → claim_packet.json (TOTALS IN CODE)│
│                                                          → HTML/PDF report + review queue    │
└───────────────────────────────┬──────────────────────────────────────────────────────────┘
                                 │  Amazon fetch (local marketplace by confirmed country)
                       ┌─────────▼──────────┐
                       │ GCP Cloud Function │  asia-south1 → India egress IP for amazon.in
                       │  cloud/amazon_fetch│  (fallback: local curl_cffi + proxy)
                       └────────────────────┘
```

## Model / method per stage

| Stage | What it does | Model / method | Produces |
|---|---|---|---|
| **Live agent** | Directs the sweep by voice; confirms locale; narrates; asks art Q; reads back | **Gemini Live** (`gemini-3.8-live`, audio), push-to-talk, function tools `set_locale` + `log_observation` | spoken coaching + live inventory + read-back |
| **Live vision** | Recognizes books/items/art on streamed frames; flags unreadable spines | **Gemini 2.5 Flash** (throttled, ~every 3 s) | live inventory rows + narration triggers |
| **A — Spines** | Detect every spine (incl. flat/stacked), read title/author, resolve edition | **Gemini 2.5 Flash** vision per sampled frame + **Google Books / OpenLibrary** for ISBN/edition | book lines w/ bbox, legibility, confidence |
| **B — Measure** | Spine height/thickness; shelf run; room floor + wall area | **ARKit plane raycasting** (no model); **ArUco fiducial** (`cv2.aruco` + solvePnP) as scale anchor/fallback; shoelace/Shapely for polygon area | cm per spine, m² per surface |
| **C — Items** | Detect & classify non-book contents; mark art `needs_appraisal` | **Gemini 2.5 Flash** vision per sampled frame | item lines w/ category, brand/model |
| **D — Pricing** | Replacement (new) from Amazon; **used value from AbeBooks**; sourced range per item | **Amazon** scrape of the confirmed country's marketplace (new) + **AbeBooks** used-book marketplace (used, localized per country); FX-convert + label `converted` when no local market; rare/signed → `needs_appraisal` | amounts + `url` + `retrieved_at` + `condition_assumed` |
| **Assembler** | Merge, dedup, compute totals, build review queue | **Pure code** (fuzzy dedup via `difflib`; totals summed) | `claim_packet.json` + report |

## Where metric scale comes from

**Not** monocular depth estimation (that would be the banned "the model estimated it").

- **Primary:** ARKit `ARWorldTrackingConfiguration` gives metric camera poses (VIO) and detected
  planes. Each spine's bounding-box corners are back-projected with the camera intrinsics and
  ray-plane intersected against the shelf-front vertical plane → metric height + thickness.
  Floor/wall area come from the plane boundary polygons (shoelace / Shapely union).
- **Scale anchor / fallback:** a printed **ArUco fiducial** of known edge length
  (`FIDUCIAL_MARKER_CM`). `cv2.aruco` + `solvePnP` recover the marker plane in camera space, so
  spines can be measured even in a frame with no usable ARKit plane, and ARKit's scale can be
  cross-checked. Recorded in the packet as `scale_method`.

ARKit conventions (camera looks down **−Z**; CV→ARKit flip) are pinned in `geometry/camera.py`
and covered by synthetic-geometry tests.

## Where prices come from

- Every price is **scraped from a real Amazon results page** on the **confirmed country's**
  marketplace (`AMAZON_DOMAINS[country]`), parsed for ASIN/title/price. The product `url` and
  `retrieved_at` date ride on every amount. A degraded/blocked page is rejected and retried;
  if nothing usable is found the amount is `null` and the line is excluded from totals.
- Fetches go through a **deployed GCP Cloud Function** in `asia-south1` (free rotating India
  egress IP for `amazon.in`); a local `curl_cffi` + proxy path is the fallback.
- **Used value** comes from **AbeBooks** (the dedicated used/second-hand/rare book marketplace),
  localized per country (`abebooks.com/.co.uk/.de/.fr/.it`). For countries with no local
  AbeBooks (IN, AE, SA, MX, TR, BR) we price on `abebooks.com` and FX-convert to the local
  currency, recording the conversion in `condition_assumed`. The cheapest *Used* offer wins.
- **Cross-market conversion:** if the local Amazon has no replacement price, we price on
  `amazon.com`, FX-convert, and set `converted: true`. FX uses a free no-key endpoint
  (`FX_LIVE=1`) or a static fallback table.
- **Rare/signed/antiquarian** copies (and anything over a value threshold) are flagged
  `needs_appraisal` and **not auto-priced**.
- **Second-country proof:** `scripts/second_country_prices.py` re-prices the same 10 books in a
  second country, proving locale is a setting (→ `ground_truth/second_country_*.md`).
- **Item ranges** are built from strong matches only (near the best match score), then
  **outlier-trimmed around the median**, so an unrelated cheap/premium SKU can't blow out the range.

## Data flow & traceability

- During the sweep, frames (JPEG + intrinsics + pose) and plane anchors are uploaded and saved
  under `data/sweeps/{id}/`. Every packet line carries a `frame_ref` pointing at a saved frame.
- `finalize` writes the capture bundle and launches `pipeline.py` as a background task.
- The app polls `GET /sweeps/{id}/summary`; once the packet exists the agent reads the summary
  back by voice. The report and `claim_packet.json` are served for in-app view and download.

## Why these choices

- **Thin native, fat Python:** the native surface is minimal (capture + AR + a voice socket);
  all intelligence lives in testable Python stages that run offline against mocked I/O.
- **Separable stages:** each stage has its own input/output and tests, so accuracy can be
  measured and tuned per stage against hand-collected ground truth.
- **Code owns the numbers:** totals, dedup and scale are code; the models only *perceive*
  (read a spine, name an item) — they never *assert a value*.
