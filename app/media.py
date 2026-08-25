"""Fetching the voice notes, photos and videos people send.

Meta doesn't put the file in the webhook - it sends you an id. Getting the
bytes is two requests: ask for the id, get back a short-lived URL, then fetch
that URL with the same token. Miss the second Authorization header and you get
a 401 on a URL that looks perfectly fine, which is a confusing hour.
"""
from __future__ import annotations

import httpx

from app.config import GRAPH_API, WHATSAPP_TOKEN

TIMEOUT = 30.0
MAX_BYTES = 16 * 1024 * 1024          # WhatsApp's own ceiling


def download(media_id: str) -> bytes:
    """Media id -> bytes. Empty on any failure; never raises."""
    if not media_id or not WHATSAPP_TOKEN:
        return b""
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            meta = client.get(f"{GRAPH_API}/{media_id}", headers=headers)
            if meta.status_code != 200:
                print(f"[media] lookup HTTP {meta.status_code}")
                return b""
            url = meta.json().get("url")
            if not url:
                return b""

            # The URL is signed but still needs the token. Yes, both.
            blob = client.get(url, headers=headers)
            if blob.status_code != 200:
                print(f"[media] fetch HTTP {blob.status_code}")
                return b""
            if len(blob.content) > MAX_BYTES:
                print("[media] oversized, ignoring")
                return b""
            return blob.content
    except Exception as err:                      # noqa: BLE001 - never fatal
        print(f"[media] {type(err).__name__}: {err}")
        return b""
