"""Backend bridge for the Gemini Live voice agent (Vertex, via ADC).

The browser can't use Vertex credentials, so we proxy: browser <-> this WebSocket <-> Gemini
Live (Python google-genai, ADC). Audio is raw PCM both ways (16 kHz up, 24 kHz down).
Push-to-talk: automatic VAD is disabled; the browser sends activity_start / activity_end.

Browser -> backend messages:
  * binary frames        : PCM16 16 kHz mic audio (only while the talk button is held)
  * {"type":"activity_start"} / {"type":"activity_end"}
  * {"type":"text","text":...}         : e.g. sweep start/stop notifications
  * {"type":"tool_result","id","name","output"}
Backend -> browser messages:
  * binary frames        : PCM16 24 kHz agent audio
  * {"type":"out_transcript"|"in_transcript","text"}
  * {"type":"tool_call","name","id","args"}
  * {"type":"turn_complete"} / {"type":"ready"} / {"type":"error","message"}
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from difflib import SequenceMatcher

from google import genai
from google.genai import types

from backend.vision import vision_extract

MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live")

# Live recognition: periodic Gemini-Flash vision on streamed frames (the live model itself is
# audio-only). Configurable — "off" = the agent directs by voice but doesn't see content.
LIVE_VISION = os.environ.get("GEMINI_LIVE_VISION", "on").lower() in ("on", "true", "1", "yes")
LIVE_VISION_MIN_INTERVAL = float(os.environ.get("LIVE_VISION_INTERVAL_S", "3.0"))

_LIVE_VISION_PROMPT = (
    "You are the eyes of a live library-sweep agent. From this ONE camera frame, briefly list "
    "books/items clearly visible and legible right now. Do NOT guess unreadable spines. "
    "Use kind 'art' for any painting, portrait, or framed picture. "
    "Also report unreadable_spines: how many book spines are clearly present but too blurry, "
    "small, or glare-washed to read (0 if the frame is clean). "
    'Return JSON: {"visible":[{"kind":"book|item|art","label":"short name"}],'
    '"unreadable_spines":0,"summary":"one short phrase"}. '
    "Empty list if nothing is clearly identifiable."
)

AGENT_INSTRUCTIONS = (
    "You are the live capture director for a home-contents insurance sweep of a book library. "
    "Keep every spoken turn to ONE short sentence. "
    "At the very start, greet the user and CONFIRM the country and currency you will price in "
    "(the app tells you the device's detected country/currency). If they confirm, or tell you a "
    "different country, call set_locale with the ISO country code and currency code so pricing "
    "uses the right local Amazon marketplace. "
    "Then direct the user where to point, note what you see, and ask only what you cannot tell "
    "from video. Never state prices or measurements — those are computed later from real sources. "
    "When you recognize something worth logging, call log_observation."
)

_LOG_OBS = types.FunctionDeclaration(
    name="log_observation",
    description="Log something recognized during the sweep so it appears on the user's screen.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "kind": types.Schema(type=types.Type.STRING),
            "label": types.Schema(type=types.Type.STRING),
            "note": types.Schema(type=types.Type.STRING),
        },
        required=["kind", "label"],
    ),
)

_SET_LOCALE = types.FunctionDeclaration(
    name="set_locale",
    description=("Set the country and currency the sweep prices in, after confirming with the "
                 "user. Drives which local Amazon marketplace is searched."),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "country": types.Schema(type=types.Type.STRING,
                                    description="ISO-3166 alpha-2, e.g. IN, US, GB, DE, AE."),
            "currency": types.Schema(type=types.Type.STRING,
                                     description="ISO-4217, e.g. INR, USD, GBP, EUR, AED."),
        },
        required=["country", "currency"],
    ),
)


def _client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
    )


def _config() -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=AGENT_INSTRUCTIONS,
        output_audio_transcription={},
        input_audio_transcription={},
        tools=[types.Tool(function_declarations=[_LOG_OBS, _SET_LOCALE])],
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        ),
    )


async def run_live_bridge(ws) -> None:
    """Bridge one browser WebSocket to one Gemini Live session until either side closes."""
    print(f"[live] bridge start; connecting to {MODEL} …")
    async with _client().aio.live.connect(model=MODEL, config=_config()) as session:
        print(f"[live] gemini session open; sending ready (live_vision={LIVE_VISION})")
        await ws.send_text(json.dumps({"type": "ready", "live_vision": LIVE_VISION}))
        last_vision = [0.0]       # throttle timestamp (vision calls)
        last_narration = [0.0]    # throttle timestamp (proactive spoken cues)
        latest_visual = [""]      # most recent "what's on camera" summary
        user_talking = [False]    # true between activity_start/activity_end (don't talk over them)
        asked_art = [False]       # ask "original or print?" once, not every framed piece
        seen_labels: list[str] = []  # fuzzy-dedup the live inventory across frames

        # How often the agent may speak an UNPROMPTED cue (logging / "step closer"). Kept
        # well above the vision interval so it narrates highlights, not every frame.
        narration_gap = float(os.environ.get("LIVE_NARRATION_GAP_S", "7.0"))

        async def _speak(instruction: str) -> None:
            """Make the live agent say a one-liner, but only if the user isn't mid-turn and
            we're off cooldown — an injected turn mustn't collide with push-to-talk."""
            now = time.monotonic()
            if user_talking[0] or now - last_narration[0] < narration_gap:
                return
            last_narration[0] = now
            try:
                await session.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text=instruction)]),
                    turn_complete=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[live] narration error (continuing): {exc!r}")

        def _is_new(label: str) -> bool:
            norm = re.sub(r"[^a-z0-9]", "", label.lower())
            if not norm:
                return False
            for n in seen_labels:
                if n == norm or n in norm or norm in n or SequenceMatcher(None, n, norm).ratio() >= 0.84:
                    return False
            seen_labels.append(norm)
            return True

        async def recognize_frame(b64: str) -> None:
            """Run Flash vision on one streamed frame; store the summary + fill the inventory.
            Context is injected just BEFORE each real user turn (see below) — injecting it
            continuously while a receiver is draining confuses the live session."""
            now = time.monotonic()
            if now - last_vision[0] < LIVE_VISION_MIN_INTERVAL:
                return
            last_vision[0] = now
            try:
                jpeg = base64.b64decode(b64)
                data = await asyncio.to_thread(vision_extract, _LIVE_VISION_PROMPT, jpeg)
            except Exception as exc:  # noqa: BLE001
                print(f"[live] vision error: {exc!r}")
                return
            visible = data.get("visible") or []
            summary = (data.get("summary") or "").strip()
            if summary:
                latest_visual[0] = summary
            # Only push items we haven't already shown (fuzzy) — no duplicate inventory rows.
            fresh = [it for it in visible if _is_new((it.get("label") or "").strip())]
            if fresh:
                await ws.send_text(json.dumps({"type": "recognition", "items": fresh}))

            # Proactively narrate: the agent should SAY what it's logging (requirement 1),
            # coach the user closer when spines are unreadable, and ASK about things it can't
            # tell from video — e.g. whether a portrait is an original or a print (requirement 4,
            # art defaults to needs_appraisal unless the user says print). All gated/throttled
            # inside _speak so they never talk over a push-to-talk turn.
            unreadable = int(data.get("unreadable_spines") or 0)
            fresh_art = [it for it in fresh if (it.get("kind") or "") == "art"]
            if fresh_art and not asked_art[0]:
                asked_art[0] = True
                name = (fresh_art[0].get("label") or "that piece").strip()
                await _speak(
                    f"You can see a framed artwork ({name}). In ONE short sentence, ask the user "
                    "whether it is an original or a print, so it can be valued correctly.")
            elif fresh:
                labels = ", ".join((it.get("label") or "").strip() for it in fresh[:3] if it.get("label"))
                if labels:
                    await _speak(
                        f"You are logging these onto the inventory right now: {labels}. "
                        "Tell the user in ONE short sentence that you're logging them. "
                        "Do not mention prices or measurements.")
            elif unreadable >= 2:
                await _speak(
                    "Several book spines in view are too blurry to read. In ONE short sentence, "
                    "ask the user to slow down and move a little closer to that shelf.")

        async def browser_to_gemini():
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    raise asyncio.CancelledError
                # A single bad input (e.g. a turn sent while one is active) must NOT tear
                # down the whole session — log and keep going.
                try:
                    data = msg.get("bytes")
                    if data is not None:
                        await session.send_realtime_input(
                            audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))
                        continue
                    text = msg.get("text")
                    if not text:
                        continue
                    ev = json.loads(text)
                    t = ev.get("type")
                    if t == "activity_start":
                        user_talking[0] = True
                        await session.send_realtime_input(activity_start=types.ActivityStart())
                    elif t == "activity_end":
                        user_talking[0] = False
                        await session.send_realtime_input(activity_end=types.ActivityEnd())
                    elif t == "text":
                        await session.send_client_content(
                            turns=types.Content(role="user", parts=[types.Part(text=ev.get("text", ""))]),
                            turn_complete=True)
                    elif t == "tool_result":
                        await session.send_tool_response(function_responses=[types.FunctionResponse(
                            id=ev.get("id"), name=ev.get("name"), response={"result": ev.get("output", "ok")})])
                    elif t == "vision_frame" and LIVE_VISION:
                        # Fire-and-forget: the ~1-2s vision call must NOT block the audio/turn
                        # message loop (that backpressure was dropping the socket mid-sweep).
                        asyncio.create_task(recognize_frame(ev.get("data", "")))
                except Exception as exc:  # noqa: BLE001
                    print(f"[live] input error (continuing): {exc!r}")

        async def gemini_to_browser():
            # session.receive() yields a turn's messages then ENDS; re-enter it for each new
            # turn (verified) — a single loop would exit after turn 1 and tear down the bridge.
            while True:
                async for m in session.receive():
                    if m.data:
                        await ws.send_bytes(m.data)
                    sc = m.server_content
                    if sc:
                        if sc.output_transcription and sc.output_transcription.text:
                            await ws.send_text(json.dumps({"type": "out_transcript", "text": sc.output_transcription.text}))
                        if sc.input_transcription and sc.input_transcription.text:
                            await ws.send_text(json.dumps({"type": "in_transcript", "text": sc.input_transcription.text}))
                        if sc.turn_complete:
                            await ws.send_text(json.dumps({"type": "turn_complete"}))
                    if m.tool_call:
                        for fc in m.tool_call.function_calls:
                            await ws.send_text(json.dumps({"type": "tool_call", "name": fc.name,
                                                           "id": fc.id, "args": dict(fc.args or {})}))

        up = asyncio.create_task(browser_to_gemini())
        down = asyncio.create_task(gemini_to_browser())
        done, pending = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
