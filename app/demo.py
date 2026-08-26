"""The Simulate Crisis button.

WHY THIS EXISTS
    Your strategy document is right about this: never type mock reports in
    front of judges during a three-minute evaluation. One click should produce
    a live, plausible crisis.

    It is also the honest way to demo. These go in as REAL MESSAGES through the
    real pipeline - extracted, merged, clustered, scored, matched to units.
    Nothing is fabricated downstream. Delete the seed and the same code serves
    real WhatsApp traffic without noticing.

    That's the difference between a demo and a mock-up, and it's worth saying
    out loud: "this isn't a fixture on the screen, it's twenty-six messages
    going through the same path your report would."
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

from app.db import get_db

# Real places across Bhubaneswar and Cuttack, with the kind of thing people
# actually type. Several clusters are deliberate: multiple reporters at one
# location, so clustering and confidence visibly do their job.
SCENARIO = [
    # --- Patia: four independent reporters, one flood -----------------------
    (20.3559, 85.8195, "Govt High School, Patia",
     "we are around 200 people at patia govt high school, no water since morning, 3 injured"),
    (20.3561, 85.8199, "Patia",
     "patia school me pani nahi hai, hum 150 log hai, 2 ghayal"),
    (20.3555, 85.8190, "Patia school",
     "no food here at patia school since yesterday, around 180 people"),
    (20.3557, 85.8193, "Patia",
     "still no water at patia school, we are 190 people, many small children with us"),

    # --- Old Town: a collapse, one reporter, low confidence ------------------
    (20.2380, 85.8340, "Old Town",
     "building collapsed near old town, 12 people trapped inside, need rescue urgently"),

    # --- Chandrasekharpur: two reporters, waterlogging -----------------------
    (20.3300, 85.8090, "Chandrasekharpur",
     "flooding at chandrasekharpur, about 60 people stuck, no drinking water, 2 elderly"),
    (20.3305, 85.8095, "Chandrasekharpur",
     "60 stranded at chandrasekharpur school, water rising, need boats"),

    # --- Nayapalli: fire ------------------------------------------------------
    (20.2870, 85.8080, "Nayapalli",
     "fire in the market at nayapalli, 3 shops burning, 2 people hurt"),

    # --- Saheed Nagar: medical -----------------------------------------------
    (20.2830, 85.8420, "Saheed Nagar",
     "my grandmother cannot walk and we are stuck on the second floor, water below"),

    # --- Cuttack: two separate events ----------------------------------------
    (20.4625, 85.8830, "Badambadi, Cuttack",
     "waterlogging at badambadi, around 40 families affected, no food"),
    (20.4700, 85.8790, "Banki, Cuttack",
     "flood water entered houses in banki, about 120 people need rescue, road is blocked"),

    # --- one that must NOT become an incident --------------------------------
    (20.2700, 85.8340, "Unit 4",
     "we are fine here, water supply is working, no help needed"),

    # --- and one that is deliberately unusable, to show the follow-up --------
    (None, None, "", "help"),
]


def _insert(conn, mid: str, frm: str, kind: str, payload: dict,
            received: datetime) -> None:
    conn.execute(
        "INSERT INTO raw_messages "
        "(wa_message_id, from_number, kind, payload, received_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        (mid, frm, kind, json.dumps(payload), received.isoformat()))


def simulate(clear: bool = False) -> dict:
    """Inject the scenario as real inbound messages.

    `clear` wipes everything first - reports, incidents, decisions - so a
    rehearsal doesn't leave yesterday's crisis on the map.
    """
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        if clear:
            for table in ("assignments", "verifications", "incident_reports",
                          "incidents", "conversations", "raw_messages"):
                conn.execute(f"DELETE FROM {table}")

        added = 0
        for i, (lat, lng, place, text) in enumerate(SCENARIO):
            # Spread them over the last two hours so recency and ordering look
            # like a real morning rather than one instant.
            when = now - timedelta(minutes=random.randint(3, 120))
            phone = f"9199000000{i:02d}"
            stamp = str(int(when.timestamp()))

            _insert(conn, f"sim.{i}.t", phone, "text", {
                "id": f"sim.{i}.t", "from": phone, "timestamp": stamp,
                "type": "text", "text": {"body": text},
            }, when)
            added += 1

            if lat is not None:
                _insert(conn, f"sim.{i}.l", phone, "location", {
                    "id": f"sim.{i}.l", "from": phone, "timestamp": stamp,
                    "type": "location",
                    "location": {"latitude": lat, "longitude": lng,
                                 "name": place},
                }, when + timedelta(seconds=20))
                added += 1

    # Build once so the caller can report what actually came out - and so a
    # judge sees the count, not a promise.
    from app import pipeline
    incidents, stuck = pipeline.build()

    return {
        "messages": added,
        "incidents": len(incidents),
        "awaiting_follow_up": len(stuck),
        "note": "Injected as real messages through the real pipeline - "
                "extracted, clustered and scored, not fabricated.",
    }
