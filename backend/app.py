"""FastAPI backend: ingest endpoints for the iOS capture app.

The iOS app calls these during and after a sweep:

* ``POST /sweeps/{id}/frame``    — one frame (JPEG + metadata), during the sweep
* ``POST /sweeps/{id}/finalize`` — sweep meta + final plane anchors, at the end
* ``GET  /sweeps/{id}/frame/{rel}`` — fetch a saved frame (lets an adjuster open a frame_ref)
* ``GET  /sweeps/{id}/bundle``   — the assembled capture bundle

The measurement / identification / pricing pipeline is triggered separately
(Stage A-D); this module is only the capture boundary.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()  # read .env so OPENAI_API_KEY etc. are available
except ImportError:
    pass

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.ingest.capture_schema import CaptureFrame, PlaneAnchor, SweepMeta
from backend.ingest.store import SweepStore

DATA_DIR = os.environ.get("DATA_DIR", "./data/sweeps")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Library Contents Claim Agent — ingest")
store = SweepStore(DATA_DIR)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/marker.png")
def marker(marker_id: int = 0) -> "Response":
    """Printable ArUco marker for spine measurement. Print it, MEASURE the black square's
    edge, and set FIDUCIAL_MARKER_CM to that size (and FIDUCIAL_DICT to the dict)."""
    from fastapi import Response

    from backend.geometry.fiducial import generate_marker_png

    dict_name = os.environ.get("FIDUCIAL_DICT", "DICT_4X4_50")
    return Response(generate_marker_png(marker_id, dict_name, px=800), media_type="image/png")


@app.get("/rootCA.pem")
def root_ca() -> FileResponse:
    """Serve the mkcert root CA so the phone can install + trust it (needed for wss)."""
    ca = Path(__file__).resolve().parent.parent / "certs" / "rootCA.pem"
    if not ca.exists():
        raise HTTPException(status_code=404, detail="rootCA.pem not found")
    return FileResponse(ca, media_type="application/x-x509-ca-cert", filename="mkcert-rootCA.crt")


@app.websocket("/live")
async def live(ws: WebSocket) -> None:
    """Bridge the browser's push-to-talk session to Gemini Live (Vertex)."""
    await ws.accept()
    from backend.live import run_live_bridge

    try:
        await run_live_bridge(ws)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:  # noqa: BLE001 - surface setup/auth errors to the client
        import traceback
        print("[live] bridge error:", repr(exc))
        traceback.print_exc()
        try:
            await ws.send_text(json.dumps({"type": "error", "message": str(exc)[:200]}))
        except Exception:  # noqa: BLE001
            pass


@app.post("/realtime/session")
async def realtime_session() -> JSONResponse:
    """Mint an ephemeral OpenAI Realtime client secret for the WebView."""
    from backend.realtime import mint_client_secret

    try:
        return JSONResponse(await mint_client_secret())
    except Exception as exc:  # noqa: BLE001 - surface config/auth errors to the client
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/sweeps/{sweep_id}/frame")
async def upload_frame(
    sweep_id: str,
    metadata: str = Form(...),  # JSON of CaptureFrame (image field is ignored/overwritten)
    image: UploadFile = File(...),
) -> JSONResponse:
    """Append one captured frame to the sweep."""
    try:
        frame = CaptureFrame.model_validate_json(metadata)
    except Exception as exc:  # noqa: BLE001 - surface validation errors to the client
        raise HTTPException(status_code=422, detail=f"bad frame metadata: {exc}") from exc
    image_bytes = await image.read()
    rel = store.save_frame(sweep_id, frame, image_bytes)
    return JSONResponse({"stored": rel})


def _run_pipeline_safe(sweep_id: str) -> None:
    """Background post-sweep processing; never raises into the request."""
    try:
        from backend.pipeline import run_pipeline

        run_pipeline(sweep_id, store, os.environ.get("OPENAI_API_KEY", ""))
    except Exception as exc:  # noqa: BLE001
        print(f"[pipeline] {sweep_id} failed: {exc}")


@app.post("/sweeps/{sweep_id}/finalize")
def finalize(sweep_id: str, meta: SweepMeta, planes: list[PlaneAnchor],
             background: BackgroundTasks) -> dict:
    """Close the sweep: write the bundle, then kick off post-sweep processing."""
    bundle = store.finalize(sweep_id, meta, planes)
    background.add_task(_run_pipeline_safe, sweep_id)
    return {"sweep_id": sweep_id, "frames": len(bundle.frames), "planes": len(bundle.planes)}


@app.get("/sweeps")
def list_sweeps() -> list[dict]:
    """List all claim sweeps (newest first) with a short summary for the claims screen."""
    out: list[dict] = []
    for d in store.root.iterdir():
        if not d.is_dir():
            continue
        sid = d.name
        pp = d / "packet.json"
        bp = d / "bundle.json"
        row = {"id": sid, "has_packet": pp.exists()}
        try:
            if pp.exists():
                p = json.loads(pp.read_text())
                row.update(captured_at=p["sweep"].get("captured_at", ""),
                           country=p["sweep"].get("country", ""), currency=p["sweep"].get("currency", ""),
                           book_count=p["totals"]["book_count"], items=len(p.get("items", [])),
                           books_cost=p["totals"]["books_replacement_cost"])
            elif bp.exists():
                m = json.loads(bp.read_text()).get("meta", {})
                row.update(captured_at=m.get("captured_at", ""), country=m.get("country", ""),
                           currency=m.get("currency", ""), book_count=0, items=0, books_cost=0)
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        out.append(row)
    out.sort(key=lambda s: s["id"], reverse=True)
    return out


@app.post("/sweeps/{sweep_id}/observation")
async def add_observation(sweep_id: str, observation: dict) -> dict:
    """Live packet sync: the agent logs an observation during the sweep; persist it."""
    store.append_observation(sweep_id, observation)
    return {"ok": True, "count": len(store.load_observations(sweep_id))}


@app.post("/sweeps/{sweep_id}/process")
def process(sweep_id: str) -> dict:
    """Run (or re-run) the pipeline synchronously — handy for testing on existing sweeps."""
    from backend.pipeline import run_pipeline

    try:
        packet = run_pipeline(sweep_id, store, os.environ.get("OPENAI_API_KEY", ""))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"book_count": packet.totals.book_count,
            "books_identified": packet.totals.books_identified}


@app.get("/sweeps/{sweep_id}/bundle")
def get_bundle(sweep_id: str) -> dict:
    try:
        return store.load_bundle(sweep_id).model_dump()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="sweep not finalized") from exc


@app.get("/sweeps/{sweep_id}/report", response_class=HTMLResponse)
def report(sweep_id: str, pdf: bool = False) -> HTMLResponse:
    """Human-readable report. Uses packet.json if the pipeline has produced one,
    otherwise builds a minimal packet from the capture bundle so the page always renders."""
    from backend.assembler import assemble_packet
    from backend.report.floorplan import render_floorplan_svg
    from backend.report.render import render_report_html
    from backend.schema import ClaimPacket, Room, Sweep

    pp = store.packet_path(sweep_id)
    processing = False
    if pp.exists():
        packet = ClaimPacket.model_validate_json(pp.read_text())
    else:
        try:
            m = store.load_bundle(sweep_id).meta
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="sweep not found") from exc
        sweep = Sweep(id=m.id, captured_at=m.captured_at, device=m.device,
                      duration_s=m.duration_s, country=m.country, currency=m.currency)
        packet = assemble_packet(sweep, Room(), [], [], 0.0)
        processing = True  # pipeline still running — the page auto-refreshes

    # Floor-plan sketch from the captured plane anchors.
    floorplan_svg = ""
    try:
        bundle = store.load_bundle(sweep_id)
        floorplan_svg = render_floorplan_svg(bundle, packet.room)
    except Exception:  # noqa: BLE001 - floor plan is best-effort
        pass
    return HTMLResponse(render_report_html(packet, sweep_id, processing=processing,
                                           floorplan_svg=floorplan_svg, pdf=pdf))


@app.get("/sweeps/{sweep_id}/packet.json")
def download_packet(sweep_id: str) -> FileResponse:
    """Download the structured claim packet."""
    p = store.packet_path(sweep_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail="packet not ready")
    return FileResponse(p, media_type="application/json", filename=f"{sweep_id}-claim_packet.json")


def _spoken_summary(p: dict) -> str:
    """A short, natural read-back of the finished packet (totals computed in code, never
    by a model). The agent speaks this within 5 minutes of the sweep ending."""
    t = p.get("totals", {})
    room = p.get("room", {})
    cur = (p.get("sweep", {}) or {}).get("currency", "") or ""
    parts: list[str] = []

    bc = t.get("book_count", 0)
    ident = t.get("books_identified", 0)
    unident = t.get("books_unidentified", 0)
    if bc:
        s = f"I logged {bc} book{'s' if bc != 1 else ''}"
        if ident:
            s += f", {ident} identified"
        if unident:
            s += f" and {unident} I could not read"
        parts.append(s + ".")
        # Name a few identified titles so the user hears WHICH books (resolved from sharp
        # frames after the sweep — never guessed live).
        titles = [b.get("title") for b in p.get("books", [])
                  if b.get("status") == "identified" and b.get("title")]
        if titles:
            named = ", ".join(titles[:3])
            more = f", and {len(titles) - 3} more" if len(titles) > 3 else ""
            parts.append(f"These include {named}{more}.")

    items = len(p.get("items", []))
    if items:
        parts.append(f"I found {items} non-book item{'s' if items != 1 else ''}.")

    rep = t.get("books_replacement_cost")
    if rep:
        parts.append(f"Estimated book replacement value is about {cur} {int(rep)}.")

    floor = room.get("floor_area_m2")
    if floor:
        parts.append(f"The room floor area is about {round(float(floor), 1)} square metres.")

    q = len(p.get("review_queue", []))
    if q:
        parts.append(f"{q} line{'s' if q != 1 else ''} need a second look in the review queue.")

    parts.append("The full claim packet is on your screen now.")
    return " ".join(parts)


@app.get("/sweeps/{sweep_id}/summary")
def sweep_summary(sweep_id: str) -> dict:
    """Read-back status: ``{"ready": bool, "text": str}``. The app polls this after a sweep
    and, once ready, has the live agent speak ``text`` back to the user."""
    p = store.packet_path(sweep_id)
    if not p.exists():
        return {"ready": False, "text": ""}
    try:
        packet = json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {"ready": False, "text": ""}
    return {"ready": True, "text": _spoken_summary(packet)}


@app.get("/sweeps/{sweep_id}/frame/{rel:path}")
def get_frame(sweep_id: str, rel: str) -> FileResponse:
    path = store.frame_path(sweep_id, rel)
    if not path.exists():
        raise HTTPException(status_code=404, detail="frame not found")
    return FileResponse(path)


# Serve the live-agent web UI (loaded inside the iOS WKWebView).
if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
