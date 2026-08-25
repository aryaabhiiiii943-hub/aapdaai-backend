"""Sending a WhatsApp message. The only outbound door.

NOTE THIS NEEDS NO TUNNEL
    Receiving needs a public URL because Meta calls you. Sending is you
    calling Meta, so it works from a laptop, a dyno, anywhere with internet.

THE 24-HOUR WINDOW - read this before wondering why the officer got nothing
    WhatsApp only lets a business send free-form text to someone who has
    messaged it in the last 24 hours. Outside that window you may only send a
    pre-approved template, and approval takes days.

    So the officer's phone cannot simply be messaged out of the blue. For the
    demo the officer sends one message to the number first - that opens the
    window - and everything after that flows normally. Say this out loud in
    judging rather than letting someone discover it: it's a platform rule,
    not a flaw in the design.
"""
from __future__ import annotations

import httpx

from app.config import GRAPH_API, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_TOKEN

TIMEOUT = 15.0

# Meta's code for "you're outside the 24-hour window". Worth naming, because
# the message that comes with it is not obvious.
OUTSIDE_WINDOW = 131047


class SendResult:
    __slots__ = ("ok", "message_id", "error", "outside_window")

    def __init__(self, ok: bool, message_id: str = "", error: str = "",
                 outside_window: bool = False):
        self.ok = ok
        self.message_id = message_id
        self.error = error
        self.outside_window = outside_window

    def __repr__(self) -> str:
        return (f"SendResult(ok={self.ok}, id={self.message_id!r}, "
                f"error={self.error!r})")


def send_text(to: str, body: str) -> SendResult:
    """Send plain text to one number.

    Never raises. A failed message must not take down the request that
    triggered it - an officer notification failing is bad, but losing the
    incident that prompted it is worse.
    """
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return SendResult(False, error="WhatsApp credentials not configured")
    if not to or not body.strip():
        return SendResult(False, error="need both a recipient and a body")

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        # preview_url off: a link preview in an emergency alert is noise, and
        # it makes Meta fetch the URL, which is a delay we don't want.
        "text": {"preview_url": False, "body": body[:4096]},
    }

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(
                f"{GRAPH_API}/{WHATSAPP_PHONE_NUMBER_ID}/messages",
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
                json=payload,
            )
    except Exception as err:                      # noqa: BLE001
        return SendResult(False, error=f"{type(err).__name__}: {err}")

    if response.status_code == 200:
        data = response.json()
        wa_id = (data.get("messages") or [{}])[0].get("id", "")
        print(f"[send] -> {to}  {wa_id}")
        return SendResult(True, message_id=wa_id)

    # Everything below is a failure we want named, not a generic 'error'.
    detail, code = response.text, 0
    try:
        err = response.json().get("error", {})
        detail = err.get("message", detail)
        code = err.get("code", 0)
    except ValueError:
        pass

    if code == OUTSIDE_WINDOW:
        print(f"[send] {to} is outside the 24h window - needs a template")
        return SendResult(False, error=detail, outside_window=True)

    if response.status_code in (401, 403):
        # 4xx: our fault, retrying changes nothing. Almost always an expired
        # temporary token - they last 24 hours.
        print(f"[send] auth rejected ({response.status_code}) - token expired?")
    else:
        print(f"[send] HTTP {response.status_code}: {detail[:120]}")

    return SendResult(False, error=detail)
