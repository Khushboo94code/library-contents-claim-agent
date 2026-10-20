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
from google import genai
from google.genai import types

from backend.vision import vision_extract

MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live")

# Live recognition: periodic Gemini-Flash vision on streamed frames (the live model itself is
# audio-only). Configurable — "off" = the agent directs by voice but doesn't see content.
LIVE_VISION = os.environ.get("GEMINI_LIVE_VISION", "on").lower() in ("on", "true", "1", "yes")
LIVE_VISION_MIN_INTERVAL = float(os.environ.get("LIVE_VISION_INTERVAL_S", "3.0"))
# Only speak a live book TITLE when the spine read is at least this confident — below it we
# say "a book" and leave the real title to the post-sweep Stage A (avoids live hallucination).
LIVE_BOOK_CONF = float(os.environ.get("LIVE_BOOK_CONF", "0.8"))

_LIVE_VISION_PROMPT = (
    "You are the eyes of a live library-sweep agent looking at ONE camera frame. "
    "List everything clearly visible, each as {\"kind\":\"item\"|\"art\"|\"book\",\"label\":"
    "\"short name\",\"confidence\":0.0}. Use 'art' for paintings/portraits/framed pictures. "
    "For a BOOK, only set the label to its real title if the spine/cover is SHARP and you can "
    "read it with high confidence — otherwise use label 'book' with a LOW confidence and never "
    "guess a title (a wrong title is worse than none). "
    "Also report book_count: total book spines visible including unreadable ones (0 if none). "
    'Return strict JSON: {"visible":[...],"book_count":0,"unreadable_spines":0,'
    '"summary":"one short phrase of the whole scene"}.'
)

AGENT_INSTRUCTIONS = (
    "You are the live capture director for a home-contents insurance sweep of a book library. "
    "Keep every spoken turn to ONE short sentence. "
    "At the start, ASK the user which country and currency they want prices in — do not assume "
    "or name a country yourself. When they tell you, call set_locale with its ISO country and "
    "currency codes so pricing uses the right local Amazon marketplace. If they don't say, "
    "pricing quietly defaults to Indian rupees. They can change it anytime, by voice or the "
    "on-screen locale chip. "
    "Then direct the user where to point and SAY WHAT YOU SEE — name the items, artworks, and "
    "any book titles you are explicitly given — so they know it's being logged. Never state "
    "prices or measurements (computed later). Never INVENT a book title: only say a title when "
    "it is given to you; for unreadable spines say 'your books' and that titles are read after "
    "the sweep. When you recognize something worth logging, call log_observation."
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


# Pin a single prebuilt voice so every turn (and every reconnect) sounds like the same
# agent — without this the model can default to a different voice per session, which is
# why the user heard "two different voices".
_VOICE = os.environ.get("GEMINI_LIVE_VOICE", "Aoede")


def _config() -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=AGENT_INSTRUCTIONS,
        output_audio_transcription={},
        input_audio_transcription={},
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=_VOICE)
            )
        ),
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
        agent_speaking = [False]  # true while the agent is mid-turn (set in gemini_to_browser)
        last_audio_ts = [0.0]     # last time agent audio arrived (safety valve for a missed turn_complete)
        asked_art = [False]       # ask "original or print?" once, not every framed piece
        announced_books = [False]  # tell the user books are being catalogued once, not per frame
        last_new_ts = [time.monotonic()]  # last time something NEW was shown (for the move nudge)
        nudged = [False]          # asked the user to move since the last new thing (don't repeat)
        seen_tokens: list[frozenset] = []  # token-set dedup of the live inventory across frames

        # How often the agent may speak an UNPROMPTED cue (logging / "step closer" / "move on").
        narration_gap = float(os.environ.get("LIVE_NARRATION_GAP_S", "7.0"))
        # How long a view may be static (nothing new) before we direct the user to move on.
        static_nudge_s = float(os.environ.get("STATIC_NUDGE_S", "6.0"))

        async def _speak(instruction: str) -> None:
            """Make the live agent say a one-liner — but never while the user is talking, while the
            agent is already mid-sentence (that caused the double-speech), or inside the cooldown."""
            now = time.monotonic()
            # Clear a stuck "speaking" flag if no audio has arrived for a couple of seconds
            # (guards against a missed turn_complete permanently muting cues).
            if agent_speaking[0] and now - last_audio_ts[0] > 2.0:
                agent_speaking[0] = False
            if user_talking[0] or agent_speaking[0] or now - last_narration[0] < narration_gap:
                return
            last_narration[0] = now
            try:
                await session.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text=instruction)]),
                    turn_complete=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[live] narration error (continuing): {exc!r}")

        def _is_new(label: str) -> bool:
            # Compare by TOKEN SET, not string order. The live model re-describes the same object
            # with reordered/variant words every pass ("purple rolled yoga mat" vs "rolled purple
            # yoga mat") — order-sensitive matching treated those as new, which both re-announced
            # the item AND kept resetting the "nothing new" clock so the move nudge never fired.
            toks = frozenset(t for t in re.split(r"[^a-z0-9]+", label.lower()) if len(t) > 2)
            if not toks:
                return False
            for seen in seen_tokens:
                inter = len(toks & seen)
                union = len(toks | seen)
                # Jaccard overlap >= 0.5 (order-independent) == same item.
                if union and inter / union >= 0.5:
                    return False
            seen_tokens.append(toks)
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
            raw = data.get("visible") or []
            book_count = int(data.get("book_count") or 0)
            unreadable = int(data.get("unreadable_spines") or 0)
            summary = (data.get("summary") or "").strip()
            if summary:
                latest_visual[0] = summary

            def _conf(it) -> float:
                try:
                    return float(it.get("confidence") or 0)
                except (TypeError, ValueError):
                    return 0.0

            # NAMEABLE things we can speak confidently: every item/artwork, plus any book whose
            # title was read with high confidence (anything lower stays an anonymous "book").
            nameable = []
            for it in raw:
                kind = (it.get("kind") or "item").lower()
                label = (it.get("label") or "").strip()
                if not label:
                    continue
                if kind == "book":
                    if _conf(it) >= LIVE_BOOK_CONF and label.lower() not in ("book", "books"):
                        nameable.append({"kind": "book", "label": label, "confidence": _conf(it)})
                    # low-confidence books fall through to the book_count acknowledgement
                else:
                    nameable.append({"kind": kind, "label": label})

            fresh = [it for it in nameable if _is_new(it["label"])]
            # Count books not confidently titled (so we can still acknowledge them).
            titled_books = sum(1 for it in fresh if it["kind"] == "book")
            anon_books = max(0, book_count - titled_books)
            new_books = anon_books > 0 and not announced_books[0]

            if fresh:
                await ws.send_text(json.dumps({"type": "recognition", "items": fresh}))
            if new_books:
                announced_books[0] = True
                await ws.send_text(json.dumps({"type": "recognition", "items": [
                    {"kind": "book", "label": "Books on the shelf", "note": "titles read after the sweep"}]}))
            if fresh or new_books:
                last_new_ts[0] = now
                nudged[0] = False

            # Narrate ONE specific sentence per pass (throttled + agent-busy-guarded). Name what
            # you actually see — items, artworks, and confidently-read book titles.
            fresh_art = [it for it in fresh if it["kind"] == "art"]
            fresh_named = [it["label"] for it in fresh]
            if fresh_art and not asked_art[0]:
                asked_art[0] = True
                await _speak(
                    f"You can see a framed artwork ({fresh_art[0]['label']}). In ONE short sentence, "
                    "ask the user whether it is an original or a print, so it can be valued correctly.")
            elif fresh_named:
                labels = ", ".join(fresh_named[:4])
                extra = (" and some books you'll read after the sweep" if new_books else "")
                await _speak(
                    f"You can now see: {labels}{extra}. In ONE short, natural sentence, tell the user "
                    "specifically what you see and that you're adding it to the inventory. Do not "
                    "mention prices or measurements.")
            elif new_books:
                await _speak(
                    "You can see books on the shelf you cannot read clearly yet. In ONE short sentence, "
                    "tell the user you've got their books and will read and price every spine after the sweep.")
            elif unreadable >= 2:
                await _speak(
                    "Several book spines in view are too blurry to read. In ONE short sentence, "
                    "ask the user to slow down and move a little closer to that shelf.")
            elif not nudged[0] and now - last_new_ts[0] > static_nudge_s:
                # Nothing new for a while → the shelf/area is done. Direct the user onward
                # (ONCE) instead of repeating the inventory.
                nudged[0] = True
                await _speak(
                    "You have logged everything currently in view. In ONE short sentence, ask the "
                    "user to slowly pan to the next shelf or area of the room.")

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
                        agent_speaking[0] = True           # agent is mid-turn — don't inject cues
                        last_audio_ts[0] = time.monotonic()
                        await ws.send_bytes(m.data)
                    sc = m.server_content
                    if sc:
                        if sc.output_transcription and sc.output_transcription.text:
                            agent_speaking[0] = True
                            last_audio_ts[0] = time.monotonic()
                            await ws.send_text(json.dumps({"type": "out_transcript", "text": sc.output_transcription.text}))
                        if sc.input_transcription and sc.input_transcription.text:
                            await ws.send_text(json.dumps({"type": "in_transcript", "text": sc.input_transcription.text}))
                        if sc.turn_complete:
                            agent_speaking[0] = False       # turn finished — cues allowed again
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
