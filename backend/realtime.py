"""Mints ephemeral OpenAI Realtime client secrets.

The real OPENAI_API_KEY stays on the backend. The iOS WebView calls
``POST /realtime/session`` to get a short-lived client secret, then connects to
OpenAI directly over WebRTC with that secret — the API key never touches the device.

Docs: https://developers.openai.com/api/docs/guides/realtime-webrtc
"""

from __future__ import annotations

import os

import httpx

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
REALTIME_MODEL = os.environ.get("REALTIME_MODEL", "gpt-realtime-2.1")
REALTIME_VOICE = os.environ.get("REALTIME_VOICE", "marin")

CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"

# The agent's persona: a capture director for an insurance contents sweep. It must
# actively steer the sweep (not narrate), ask what it cannot see, and never invent prices.
AGENT_INSTRUCTIONS = """
You are the live capture director for a home-contents insurance sweep of a book library.
The user is walking a room once with their phone camera on. Your job:

- Greet briefly, confirm the country and currency, explain the sweep in one sentence.
- Actively DIRECT the capture. When a shelf looks blurry, far, glared, or rushed, say so
  immediately and concretely: "slow down", "step closer to that shelf", "hold on the top row".
- Tell the user what you are logging as you see it ("logging the row of orange Penguins").
- Ask only questions you cannot answer from video, e.g. "is that framed picture an original
  or a print?" Keep turns short — the user is moving.
- Accept corrections by voice ("that's a first edition", "skip that shelf").
- NEVER state a price or a measurement as fact. Pricing and measurement happen later in the
  backend from real sources. If asked a price, say it will be priced from live sources after.
Be concise, warm, and practical. One or two sentences per turn.
""".strip()


async def mint_client_secret() -> dict:
    """Create an ephemeral Realtime session secret. Raises if no key configured."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set — add it to .env")

    body = {
        "session": {
            "type": "realtime",
            "model": REALTIME_MODEL,
            "instructions": AGENT_INSTRUCTIONS,
            "audio": {"output": {"voice": REALTIME_VOICE}},
        }
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(CLIENT_SECRETS_URL, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()
