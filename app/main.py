"""The front door. Meta talks to this file and nothing else.

Two jobs only:
  1. prove to Meta that this endpoint is ours  (GET)
  2. take a message, store it raw, answer fast (POST)

Deliberately stupid. No parsing, no decisions, no AI. Everything this file
knows how to do is receive.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response

from app import notify as notify_mod
from app import pipeline, store
from app.config import WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN
from app.db import already_seen, get_db, init_db

app = FastAPI(title="AapdaAi ingestion")
router = APIRouter()


@app.on_event("startup")
def startup() -> None:
    init_db()


@router.get("/webhook")
def verify(request: Request) -> Response:
    """Meta's one-time handshake.

    Meta calls this with the verify token you typed into their dashboard. Echo
    the challenge back and it accepts the URL. Anything else, 403 - because
    this is also how you refuse someone who guessed your URL.
    """
    params = request.query_params
    if (params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == WHATSAPP_VERIFY_TOKEN):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    return Response(content="forbidden", status_code=403)


@router.post("/webhook")
async def receive(request: Request) -> Response:
    """Every message lands here.

    Note what this does NOT do: interpret, reply, or fail. It stores and gets
    out of the way. Answering slowly makes Meta resend; answering 500 makes
    Meta resend the same broken payload forever.
    """
    body = await request.json()
    senders: set[str] = set()

    for message in _messages_in(body):
        wa_id = message.get("id")
        if not wa_id:
            continue
        with get_db() as conn:
            if already_seen(conn, wa_id):
                continue
            conn.execute(
                "INSERT INTO raw_messages "
                "(wa_message_id, from_number, kind, payload, received_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    wa_id,
                    message.get("from", ""),
                    message.get("type", "unknown"),
                    json.dumps(message),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        print(f"[recv] {message.get('type')} from {message.get('from')}")
        senders.add(message.get("from", ""))

    # Reply to each person once, after all their messages in this payload are
    # stored - not once per message, or someone who sends text and a pin gets
    # two answers.
    #
    # This runs inline, which is fine at demo scale but is the wrong shape for
    # real traffic: Meta gives us seconds to answer, and a slow send here
    # would make it retry. The fix is a background task, not more speed.
    for sender in senders:
        if sender:
            try:
                pipeline.respond_to(sender)
            except Exception as err:              # noqa: BLE001
                # A failed reply must never turn into a non-200, or Meta
                # resends the message and we process it all over again.
                print(f"[reply] {type(err).__name__}: {err}")

    return Response(content="ok", status_code=200)


def _messages_in(body: dict) -> list[dict]:
    """Dig out the messages addressed to OUR number, and only ours.

    Their shape is entry[] -> changes[] -> value.messages[], and any level can
    be missing - status callbacks come through this same endpoint with no
    messages at all. Hence .get() the whole way down rather than try/except.

    The phone_number_id check is not optional. This WhatsApp Business Account
    also carries the restaurant bot's number, so real customer conversations
    arrive here too. Storing them would put a client's customers in a hackathon
    database, and neither they nor the client agreed to that.
    """
    out: list[dict] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            recipient = value.get("metadata", {}).get("phone_number_id")
            if WHATSAPP_PHONE_NUMBER_ID and recipient != WHATSAPP_PHONE_NUMBER_ID:
                print(f"[skip] not our number ({recipient})")
                continue
            out.extend(value.get("messages", []))
    return out


@router.get("/health")
def health() -> dict:
    return {"ok": True}


# --- what the dashboard reads ------------------------------------------------

@router.get("/incidents")
def incidents(llm: bool = False) -> dict:
    """Ranked incidents, worst first.

    Two numbers per incident, never merged: `severity` is how bad it is if
    true, `confidence` is how sure we are. An unconfirmed report of something
    catastrophic and a confirmed minor one must not look alike.

    `?llm=true` runs the model over the reports the parser left holes in.
    Off by default so the endpoint stays fast and works with no API key.
    """
    return {"incidents": pipeline.briefs(use_llm=llm)}


@router.get("/follow-ups")
def follow_ups(llm: bool = False) -> dict:
    """Reports we can't act on yet, and the one question to ask each person."""
    return {"follow_ups": pipeline.follow_ups(use_llm=llm)}


@router.post("/incidents/{incident_id}/verify")
def verify(incident_id: int, body: dict) -> dict:
    """A named officer decides whether this is real.

    Four outcomes, not two. `ground_check` means send one person to look -
    itself a dispatch, but the cheapest one available. That is the honest
    answer to "what if the report is fake": we spend one bike, not one
    ambulance.

    Nothing expensive should move before this endpoint has been called.
    """
    decided_by = (body.get("decided_by") or "").strip()
    decision = (body.get("decision") or "").strip()
    if not decided_by:
        raise HTTPException(422, "decided_by is required - decisions are "
                                 "attributable or they are not decisions")
    try:
        return store.verify(incident_id, decided_by, decision,
                            body.get("note", ""))
    except ValueError as err:
        raise HTTPException(422, str(err)) from err
    except LookupError as err:
        raise HTTPException(404, str(err)) from err


@router.patch("/incidents/{incident_id}")
def update(incident_id: int, body: dict) -> dict:
    """The other lifecycle: pending -> assigned -> in_progress -> resolved."""
    try:
        return store.set_response(incident_id, (body.get("response") or "").strip())
    except ValueError as err:
        raise HTTPException(422, str(err)) from err


@router.get("/incidents/{incident_id}/history")
def history(incident_id: int) -> dict:
    """Who decided what, and when."""
    return {"history": store.history(incident_id)}


@router.post("/incidents/{incident_id}/notify")
def notify(incident_id: int, body: dict | None = None) -> dict:
    """Push this incident's recommendation to an officer's WhatsApp.

    Sending needs no tunnel - this is us calling Meta. It does need the
    officer to have messaged the number within the last 24 hours, which is a
    WhatsApp platform rule, not ours.
    """
    incidents, _ = pipeline.build()
    match = next((i for i in incidents if i.id == incident_id), None)
    if match is None:
        raise HTTPException(404, f"no incident {incident_id}")

    result = notify_mod.notify_officer(match, (body or {}).get("to", ""))
    return {
        "sent": result.ok,
        "message_id": result.message_id,
        "error": result.error,
        "outside_24h_window": result.outside_window,
        "preview": notify_mod.officer_message(match),
    }


@router.get("/incidents/{incident_id}/preview")
def preview(incident_id: int) -> dict:
    """See exactly what the officer would receive, without sending it.

    Useful while building, and useful on stage - you can show the message
    before you send it.
    """
    incidents, _ = pipeline.build()
    match = next((i for i in incidents if i.id == incident_id), None)
    if match is None:
        raise HTTPException(404, f"no incident {incident_id}")
    return {"message": notify_mod.officer_message(match)}


@router.get("/stats")
def stats() -> dict:
    """The tiles at the top of the dashboard."""
    briefs = pipeline.briefs()
    return {
        "incidents": len(briefs),
        "critical": sum(1 for b in briefs if b["severity_band"] == "critical"),
        "people_affected": sum(b["people"] or 0 for b in briefs),
        "awaiting_confirmation": sum(
            1 for b in briefs if b["confirmation"] == "unconfirmed"),
    }


app.include_router(router)
