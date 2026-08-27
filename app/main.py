"""The front door. Meta talks to this file and nothing else.

Two jobs only:
  1. prove to Meta that this endpoint is ours  (GET)
  2. take a message, store it raw, answer fast (POST)

Deliberately stupid. No parsing, no decisions, no AI. Everything this file
knows how to do is receive.
"""
import json
from datetime import datetime, timezone

from fastapi import (APIRouter, BackgroundTasks, FastAPI, HTTPException,
                     Request, Response)
from fastapi.middleware.cors import CORSMiddleware

from app import assistance, demo, intake, inventory_seed
from app import notify as notify_mod
from app import pipeline
from app import resources as inventory
from app import store
from app.config import WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN
from app.db import already_seen, get_db, init_db

app = FastAPI(title="AapdaAi ingestion")

# The dashboard is served from a different origin than this API, so without
# this a browser refuses every request before it leaves the machine - and the
# error it shows blames CORS rather than anything you can see in these logs.
#
# allow_origins is a list, not "*", because "*" plus credentials is rejected by
# browsers and because naming the origins is the honest version of this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://aapat.freebuff.app",
        "http://localhost:3000",       # the dashboard running locally
        "http://localhost:5173",       # vite's default
    ],
    # Any Render static site or Vercel preview. Loose, and deliberately so:
    # this API has no auth yet, so CORS is not what's protecting it - and a
    # blocked origin at 9am on demo day costs more than it saves. Tighten to
    # the exact domain once there's a real one.
    allow_origin_regex=r"https://.*\.(onrender\.com|vercel\.app|netlify\.app)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
async def receive(request: Request, background: BackgroundTasks) -> Response:
    """Every message lands here.

    Note what this does NOT do: interpret, reply, or fail. It stores and gets
    out of the way. Answering slowly makes Meta resend; answering 500 makes
    Meta resend the same broken payload forever.
    """
    body = await request.json()
    # phone -> the text of their most recent message in this payload, so a
    # reply of "2" can be read as the answer to what we last asked them.
    senders: dict[str, str] = {}

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
        body_text = (message.get("text") or {}).get("body", "")
        senders[message.get("from", "")] = body_text or senders.get(
            message.get("from", ""), "")

    # Reply to each person once, after all their messages in this payload are
    # stored - not once per message, or someone who sends text and a pin gets
    # two answers.
    #
    # AFTER we answer Meta, not before. Working out the reply can involve a
    # model call and an outbound send; doing that inside Meta's timeout window
    # means a slow moment turns into a retry, and the retry arrives while the
    # first one is still running. Storing is fast and must block; thinking is
    # slow and must not.
    for sender, text in senders.items():
        if sender:
            background.add_task(_reply, sender, text)

    return Response(content="ok", status_code=200)


def _reply(sender: str, text: str) -> None:
    """Runs after the 200 has gone back to Meta. Never allowed to raise."""
    try:
        pipeline.respond_to(sender, text)
    except Exception as err:                      # noqa: BLE001
        print(f"[reply] {type(err).__name__}: {err}")


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


@router.get("/")
def index() -> dict:
    """A map of the service.

    Exists because opening the bare URL used to return {"detail":"Not Found"},
    which looks like a broken deployment and is really just a missing route.
    """
    return {
        "service": "AapdaAi ingestion",
        "docs": "/docs  <- clickable version of everything below",
        "read": {
            "GET /health": "is it awake",
            "GET /incidents": "ranked incidents, severity + confidence",
            "GET /follow-ups": "reports we can't act on yet, and what to ask",
            "GET /stats": "dashboard tiles",
            "GET /resources": "ambulances, teams, trucks, boats",
            "GET /facilities": "hospitals and shelters",
            "GET /roadblocks": "what's impassable",
        },
        "write": {
            "POST /reports": "web form / 112 operator intake",
            "POST /incidents/{id}/verify": "a named human confirms or rejects",
            "POST /incidents/{id}/notify": "send the officer the brief",
            "POST /incidents/{id}/assign": "commit a named unit",
            "POST /demo/seed": "populate the inventory",
        },
    }


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


@router.post("/reports")
def submit_report(body: dict) -> dict:
    """A report from anywhere that isn't WhatsApp.

    The public form, and the 112 operator's console - an operator taking a
    phone call types what they hear here and it joins the same queue as
    everything else.

    `source` sets the trust weight: an identified responder outweighs an
    anonymous form, and both are turned down rather than off.
    """
    try:
        return intake.submit(
            text=str(body.get("text", "")),
            lat=body.get("lat"),
            lng=body.get("lng"),
            place=str(body.get("place", "")),
            phone=str(body.get("phone", "")),
            source=str(body.get("source", "web")),
            reported_by=str(body.get("reported_by", "")),
            photos=body.get("photos") or [],
        )
    except ValueError as err:
        raise HTTPException(422, str(err)) from err


# --- inventory ---------------------------------------------------------------

@router.get("/resources")
def list_resources(kind: str = "", status: str = "") -> dict:
    """Ambulances, rescue teams, fire trucks, boats, heavy rescue.

    Seeded, deliberately. No Indian state publishes live positions over an
    API - what's real here is the shape, so a 108/ERSS feed replaces the seed
    and nothing above it changes.
    """
    return {"resources": inventory.all_resources(kind=kind, status=status),
            "counts": inventory.counts()}


@router.get("/facilities")
def list_facilities(kind: str = "") -> dict:
    """Hospitals and shelters. Fixed, and they fill up rather than get busy."""
    return {"facilities": inventory.facilities(kind=kind)}


@router.get("/roadblocks")
def list_roadblocks() -> dict:
    return {"road_blocks": inventory.road_blocks()}


@router.post("/incidents/{incident_id}/assign")
def assign(incident_id: int, body: dict, background: BackgroundTasks) -> dict:
    """Commit a named unit, take it out of the pool, and tell the reporters.

    Three things happen here and all three matter:

      * the unit is committed, so it stops being offered to the next incident
      * the incident's response state moves to 'assigned'
      * EVERYONE who reported it hears that something is coming, by name

    That last one is the loop closing. Four people reported the Patia flood;
    telling one and leaving three in silence sends the other three back to
    calling 112.

    The message goes out in the background - a slow send must not make the
    officer's button appear to fail.
    """
    try:
        result = inventory.assign(incident_id, int(body.get("resource_id", 0)),
                                  body.get("purpose", "response"))
    except LookupError as err:
        raise HTTPException(404, str(err)) from err
    except (ValueError, TypeError) as err:
        raise HTTPException(409, str(err)) from err

    store.set_response(incident_id, "assigned")
    background.add_task(_tell_reporters, incident_id, result.get("unit", ""))
    result["reporters_notified"] = True
    return result


def _tell_reporters(incident_id: int, unit: str) -> None:
    """Runs after the officer's request has already returned."""
    try:
        incidents, _ = pipeline.build()
        match = next((i for i in incidents if i.id == incident_id), None)
        if match:
            notify_mod.help_dispatched(match, [unit])
    except Exception as err:                      # noqa: BLE001
        print(f"[notify] {type(err).__name__}: {err}")


@router.post("/resources/{resource_id}/release")
def release(resource_id: int) -> dict:
    return inventory.release(resource_id)


@router.post("/demo/seed")
def demo_seed(force: bool = False) -> dict:
    """Populate the resource inventory. Safe to call twice."""
    return inventory_seed.seed(force=force)


@router.post("/demo/simulate")
def demo_simulate(clear: bool = False) -> dict:
    """SIMULATE CRISIS. One click, a plausible morning across two districts.

    These go in as real inbound messages and come out the far end having been
    extracted, merged, clustered, scored and matched to units - the same path a
    real WhatsApp report takes. Nothing is fabricated downstream.

    `?clear=true` wipes first, so a rehearsal doesn't leave yesterday's crisis
    on the map.
    """
    inventory_seed.seed()          # units must exist for anything to dispatch
    return demo.simulate(clear=clear)


@router.get("/unassisted")
def unassisted() -> dict:
    """Reports nobody has reached yet — oldest first.

    The number a control room cannot produce today. Sorted by how long they've
    been waiting rather than by severity, on purpose: a small emergency nobody
    has touched for three hours is a worse failure than a large one being
    actively worked.
    """
    incidents, _ = pipeline.build()
    return {"unassisted": assistance.unassisted(incidents)}


@router.post("/incidents/{incident_id}/check-arrival")
def check_arrival(incident_id: int) -> dict:
    """Ask everyone who reported it whether help actually reached them.

    The only honest way to close a report. "We sent a truck" is not the same
    as "it got there", and only the people standing there know which.
    """
    incidents, _ = pipeline.build()
    match = next((i for i in incidents if i.id == incident_id), None)
    if match is None:
        raise HTTPException(404, f"no incident {incident_id}")
    asked = assistance.ask_arrival(match)
    return {"incident_id": incident_id, "asked": asked,
            "reporters": match.reporters}


@router.get("/stats")
def stats() -> dict:
    """The tiles at the top of the dashboard."""
    briefs = pipeline.briefs()
    counts = inventory.counts()
    return {
        "incidents": len(briefs),
        "critical": sum(1 for b in briefs if b["severity_band"] == "critical"),
        "people_affected": sum(b["people"] or 0 for b in briefs),
        "awaiting_confirmation": sum(
            1 for b in briefs if b["confirmation"] == "unconfirmed"),
        "resources_available": counts["available"],
        "resources_deployed": counts["deployed"],
        # The tile that matters most and that nobody else can produce.
        "unassisted": sum(1 for b in briefs
                          if b.get("assistance") in ("unassisted", "unreachable")),
        "arrival_confirmed": sum(1 for b in briefs
                                 if b.get("assistance") == "arrived"),
    }


app.include_router(router)
