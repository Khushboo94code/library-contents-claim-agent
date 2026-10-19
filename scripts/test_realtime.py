"""Live probe of the OpenAI Realtime API over WebSocket.

Validates the exact things the browser client (web/agent.js) depends on:
  - session.update schema (turn_detection, tools) is accepted (no error)
  - the real event type NAMES for response lifecycle + transcripts + tool calls
  - a text turn actually produces a completed response
  - a tool-triggering prompt actually emits function-call events

This hits the live API (costs a few cents). Run manually:
    ./.venv/bin/python scripts/test_realtime.py
"""

from __future__ import annotations

import asyncio
import json
import os

from dotenv import load_dotenv
import websockets

load_dotenv()
KEY = os.environ["OPENAI_API_KEY"]
MODEL = os.environ.get("REALTIME_MODEL", "gpt-realtime-2.1")
URL = f"wss://api.openai.com/v1/realtime?model={MODEL}"

TOOLS = [{
    "type": "function",
    "name": "log_observation",
    "description": "Log something recognized during the sweep.",
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["book", "item", "shelf", "note"]},
            "label": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["kind", "label"],
    },
}]


async def main():
    seen_types = {}
    errors = []
    transcript = []
    tool_calls = []

    async with websockets.connect(URL, additional_headers={"Authorization": f"Bearer {KEY}"}) as ws:
        print(f"connected to {MODEL}")

        async def recv_loop(stop_after):
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=stop_after)
                except asyncio.TimeoutError:
                    return
                ev = json.loads(raw)
                t = ev.get("type", "?")
                seen_types[t] = seen_types.get(t, 0) + 1
                if t == "error":
                    errors.append(ev.get("error", ev))
                    print("  ERROR:", json.dumps(ev.get("error", ev))[:300])
                if "transcript" in t and (ev.get("delta") or ev.get("transcript")):
                    transcript.append(ev.get("delta") or ev.get("transcript"))
                if "function_call" in t:
                    print("  fn event:", t, json.dumps(ev)[:200])
                    if t.endswith("arguments.done"):
                        tool_calls.append({"name": ev.get("name"), "args": ev.get("arguments")})

        # 1) configure the session exactly like the client does
        await ws.send(json.dumps({"type": "session.update", "session": {
            "type": "realtime",
            "instructions": "You are a terse capture director. One short sentence per turn.",
            "tools": TOOLS,
            "tool_choice": "auto",
            "audio": {"input": {
                "noise_reduction": {"type": "near_field"},
                "turn_detection": None,
            }},
        }}))

        # 2) a plain text turn -> expect a completed response + transcript
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Say hello in one sentence."}]}}))
        await ws.send(json.dumps({"type": "response.create"}))
        await recv_loop(stop_after=8)

        # 3) a prompt that should trigger the log_observation tool
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text",
                         "text": "You see a shelf of orange Penguin paperbacks. Log it with the log_observation tool."}]}}))
        await ws.send(json.dumps({"type": "response.create"}))
        await recv_loop(stop_after=10)

    print("\n=== EVENT TYPES OBSERVED ===")
    for t in sorted(seen_types):
        print(f"  {seen_types[t]:3d}  {t}")
    print("\n=== RESPONSE LIFECYCLE NAMES (what agent.js must match) ===")
    for t in sorted(seen_types):
        if t.startswith("response.") and ("created" in t or "done" in t or "cancel" in t):
            print("  ", t)
    print("\ntranscript:", "".join(transcript)[:200] or "(none)")
    print("tool_calls:", tool_calls or "(none)")
    print("errors:", errors or "(none)")


if __name__ == "__main__":
    asyncio.run(main())
