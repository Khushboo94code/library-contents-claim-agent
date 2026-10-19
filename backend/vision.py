"""Provider-agnostic vision-extraction helper used by Stage A (books) and Stage C (items).

Default provider is Gemini on Vertex AI (uses ADC — no API key in code). OpenAI is kept as a
fallback. Both take a prompt + one image and return parsed JSON, so the stages don't care
which model is behind it.
"""

from __future__ import annotations

import base64
import json
import os

_GEMINI = None


def _gemini_client():
    global _GEMINI
    if _GEMINI is None:
        from google import genai

        _GEMINI = genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    return _GEMINI


def vision_extract(prompt: str, image_bytes: bytes, *, provider: str | None = None,
                   model: str | None = None) -> dict:
    """Run a vision model on one image and return the parsed JSON object it emits."""
    provider = provider or os.environ.get("VISION_PROVIDER", "gemini")

    if provider == "gemini":
        from google.genai import types

        model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        resp = _gemini_client().models.generate_content(
            model=model,
            contents=[prompt, types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")],
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
        )
        return json.loads(resp.text)

    # --- OpenAI fallback ---
    import httpx

    key = os.environ.get("OPENAI_API_KEY", "")
    model = model or os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini")
    data_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()
    resp = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}}]}],
        },
        timeout=90,
    )
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])
