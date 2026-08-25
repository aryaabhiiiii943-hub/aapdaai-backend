"""The AI layer: reading what the parser couldn't, and hearing voice notes.

TWO RULES, BOTH LEARNED THE HARD WAY

  1. This layer is OPTIONAL. No key, no network, model down, malformed reply -
     the service still runs on the rules parser. An AI feature that can take
     the whole system down with it is a liability on a stage.

  2. It EXTRACTS, it does not JUDGE. It reads "we are about two hundred" and
     returns 200. It never decides how urgent something is or what to send.
     Those are arithmetic and a human's call, in that order.

Raw httpx, no vendor SDK - same shape as storyteller's llm.py, so the retry and
error handling are things you can read rather than things a library hides.
"""
from __future__ import annotations

import json
import re

import httpx

from app.config import (GROQ_API, GROQ_API_KEY, GROQ_AUDIO_MODEL,
                        GROQ_TEXT_MODEL)

TIMEOUT = 20.0


def available() -> bool:
    return bool(GROQ_API_KEY)


# --- text -------------------------------------------------------------------

SYSTEM = """You read short emergency messages from people caught in a disaster \
in India. They write in English, Hindi, Odia, or a mix, often in Roman script, \
often badly, often panicking.

Return ONLY a JSON object with these keys:
  headcount   integer or null - how many people are AT that place
  injured     integer or null - how many are hurt
  trapped     integer or null - how many are stuck and cannot get out
  deficits    array of any of: "water", "food", "medical", "shelter", "rescue"
  place_text  string - the place they name, verbatim, or ""

RULES
- Report only what the message states or plainly implies. Never estimate.
- Words count: "do sau log" is 200, "a dozen" is 12, "kuch log" is null.
- deficits are what they LACK or ASK FOR. "water is working" is not a deficit.
- Someone saying they are safe returns nulls and an empty array.
- No prose, no markdown, no explanation. JSON only."""


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a reply.

    Models add prose before the JSON no matter how firmly you ask them not to.
    Strict prompt AND tolerant parser - you need both.
    """
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        return None


def read_message(text: str) -> dict | None:
    """Message -> the same fields the rules parser produces, or None."""
    if not available() or not text.strip():
        return None
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(
                f"{GROQ_API}/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": GROQ_TEXT_MODEL,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": text[:2000]},
                    ],
                },
            )
        if response.status_code != 200:
            print(f"[llm] HTTP {response.status_code} - falling back to rules")
            return None
        content = response.json()["choices"][0]["message"]["content"]
        return _extract_json(content)
    except Exception as err:                      # noqa: BLE001 - never fatal
        print(f"[llm] {type(err).__name__}: {err} - falling back to rules")
        return None


# --- voice ------------------------------------------------------------------

def transcribe(audio: bytes, filename: str = "note.ogg") -> str:
    """Voice note -> text. Empty string on any failure.

    Whisper detects the language itself, which matters here: nobody in a flood
    is going to switch their keyboard to English. Hindi transcribes well, Odia
    less so - worth saying out loud rather than pretending otherwise.
    """
    if not available() or not audio:
        return ""
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{GROQ_API}/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": (filename, audio, "application/octet-stream")},
                data={"model": GROQ_AUDIO_MODEL, "response_format": "json"},
            )
        if response.status_code != 200:
            print(f"[whisper] HTTP {response.status_code}")
            return ""
        return response.json().get("text", "").strip()
    except Exception as err:                      # noqa: BLE001
        print(f"[whisper] {type(err).__name__}: {err}")
        return ""
