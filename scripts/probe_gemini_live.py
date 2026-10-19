"""Probe Vertex Gemini Live API: find a working live model + confirm the SDK shape.

Run manually (uses ADC + Vertex): ./.venv/bin/python scripts/probe_gemini_live.py
"""

import asyncio
import os

from google import genai
from google.genai import types

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
LOC = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
client = genai.Client(vertexai=True, project=PROJECT, location=LOC)

CANDIDATES = [
    "gemini-live-2.5-flash",
    "gemini-live-2.5-flash-preview-native-audio",
    "gemini-2.0-flash-live-preview-04-09",
    "gemini-2.0-flash-live-001",
    "gemini-2.5-flash-live",
    "gemini-3.8-live",
]


async def try_model(m: str) -> str:
    try:
        cfg = types.LiveConnectConfig(response_modalities=["TEXT"])
        async with client.aio.live.connect(model=m, config=cfg) as s:
            await s.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text="Say hi in 3 words")]))
            out = []
            async for msg in s.receive():
                if msg.text:
                    out.append(msg.text)
                if msg.server_content and msg.server_content.turn_complete:
                    break
            return "OK: " + ("".join(out).strip()[:60] or "(no text)")
    except Exception as e:  # noqa: BLE001
        return "FAIL: " + str(e)[:140]


async def main():
    for m in CANDIDATES:
        print(f"{m:45s} -> {await try_model(m)}")


if __name__ == "__main__":
    asyncio.run(main())
