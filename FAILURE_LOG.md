# Failure Log

Three worst errors observed in a real run (`sweep-1792493169` — a dense yoga/ayurveda
library of ~50 spines), their root cause, and what I changed / would change. Latency is
measured; cost is estimated from call counts.

## The three worst errors

### 1. Confident book-title hallucination (accuracy)
**What happened.** One motion-blurred frame of a book *stack* produced five famous
bestsellers — *Atomic Habits*, *The Alchemist*, etc. — each at **0.95 confidence**. None
were on the shelf. The packet claimed 9 identified books when 3 were real.
**Root cause.** The vision model reads an illegible/blurred spine as the *nearest plausible
famous title* and still reports high confidence. A single frame was trusted as ground truth,
so one bad frame injected five fabricated titles.
**Changed (shipped).** Cross-frame corroboration (`MIN_ID_FRAMES=2`): a title must be read in
≥2 distinct frames to be "identified"; uncorroborated reads drop to *unidentified* (kept with
measured dimensions, no guessed title). The five fakes — all single-frame — were dropped.
**Would change next.** Replace frame-count with **per-spine crop re-verification**: re-read the
title's bounding box in isolation; a hallucination doesn't reproduce the same title, a real
read does. This kills fakes *without* the recall cost of #3.

### 2. Used value priced above new (pricing integrity)
**What happened.** 19 of 33 books had an AbeBooks "used" value **higher than the new Amazon
price** — e.g. *The Bhagavadgita* new ₹156 vs "used" ₹3,735, and *Yoga Makaranda* "used"
₹15,617. Six more books came back at exactly ₹83 ($1.00).
**Root cause.** The AbeBooks keyword search drifted to *different, pricier editions*
(scholarly/collectible) with no check that the listing was the same book, and "sort price
low→first" surfaced **$1 ship-cost placeholder listings**. Nothing bounded used ≤ new.
**Changed (shipped).** Require the listing title to share ≥50% of the book's title tokens
(`ABE_TITLE_MATCH`), drop sub-$3 junk (`ABE_MIN_PRICE`), and a hard rule: a used copy may never
exceed the new replacement — if it does it's a mismatch and is dropped. **Inversions 19 → 0.**
**Would change next.** Match on **ISBN/edition** rather than keywords, and report a used
*range* (median of matched offers) instead of the single cheapest listing.

### 3. Corroboration over-correction — recall loss on dense shelves
**What happened.** The fix for #1 then dropped ~21 of 24 *real* titles on a densely-packed
shelf, and Stage A identification swings run-to-run (33 → 8 identified across reruns of the
same sweep).
**Root cause.** On a fast single pass, a real niche book and a hallucination both appear in
only ~1 sharp frame, so frame-count can't separate them. Blur doesn't separate them either —
measured Laplacian variance put a *real* frame (273) below the hallucination frame (495).
**Would change.** Per-spine crop re-verification (as in #1) instead of frame-count; a slower
guided pan (the live agent already nudges "move to the next shelf"); and higher frame sampling
on dense shelves — trading ~tens of seconds of latency for recall.

> Operational honorable mention: after I fixed the pricing code, the **already-running backend
> kept serving the old logic** (no hot-reload), so a re-run re-introduced the inversions until
> restart. Fix: `--reload` in dev + a code-version stamp written into every packet.

## Latency per stage — measured (`sweep-1792493169`, 264 frames)

| Stage | Method | Latency |
|---|---|---|
| Capture → upload | live, during the sweep | < 3 min (live) |
| Scale solve | ARKit poses / fiducial | 0.1 s |
| **A — Spine ID** | Gemini 2.5 Flash × 24 sampled frames (8 parallel) | **69.9 s** |
| **B — Measure** | ARKit plane raycast + shoelace area (code) | < 0.1 s |
| **C — Items** | Gemini 2.5 Flash × 24 sampled frames (8 parallel) | **75.8 s** |
| **D — Price books** | Amazon + AbeBooks scrape (8 parallel) | 6.0 s |
| **D — Price items** | Amazon scrape (8 parallel) | 48.8 s |
| **Total post-finalize** | | **≈ 200 s (3.3 min)** |

Comfortably inside the 5-minute bar. A and C are independent vision stages run sequentially
today; running them concurrently would cut ~70 s off the total.

## Cost per sweep — estimated

| Component | Basis | Est. cost |
|---|---|---|
| Pipeline vision (A + C) | 48 Gemini 2.5 Flash image calls (~1k in / 0.3k out each) | ~$0.10 |
| Live vision | ~60 throttled frame calls (every ~3 s over the sweep) | ~$0.07 |
| Live audio agent | ~4 min Gemini Live audio in/out (**dominant cost**) | ~$0.50–1.00 |
| Amazon / AbeBooks / FX | free-tier GCP Cloud Function + no-key FX endpoint | ~$0 |
| **Total** | | **≈ $0.70–1.20 / sweep** |

Cost is dominated by the live voice agent, not the vision pipeline. If voice were made
optional (text-only direction), a sweep would cost ~$0.10. Figures are estimates from call
counts and public per-token rates, not a billing export.
