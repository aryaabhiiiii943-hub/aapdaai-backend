"""Fake inbound messages, so the pipeline can be built without the tunnel.

Everything downstream of raw_messages cannot tell these apart from real ones -
they are the exact shape Meta sends. Same idea as run_reference.py in
storyteller: replace the one stage that's blocked, and the rest runs for free.

    python seed.py          insert the messages
    python seed.py --show   extract each one and print what we understood
    python seed.py --clear  wipe raw_messages and start over
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from app.db import get_db, init_db
from app.extract import extract, group_by_reporter

PHONE_ID = "1245719618618905"          # our test number
NOW = int(datetime.now(timezone.utc).timestamp())

# Bhubaneswar. Patia and Chandrasekharpur are ~1.5 km apart, so the first three
# should cluster together and the Old Town one should not.
MESSAGES = [
    ("919000000001", "text", NOW - 600,
     "we are around 200 people at Patia govt high school, no water since morning, 3 injured"),

    ("919000000002", "text", NOW - 480,
     "Patia school me pani nahi hai, hum 150 log hai, 2 ghayal"),

    ("919000000003", "text", NOW - 300,
     "no food here at patia school since yesterday, around 180 people"),

    ("919000000004", "text", NOW - 240,
     "Building collapsed near Old Town, 12 people trapped inside, need rescue urgently"),

    ("919000000005", "text", NOW - 120,
     "we are fine here, water supply is working"),          # must NOT become a need

    ("919000000006", "text", NOW - 60,
     "help"),                                               # incomplete - triggers follow-up
]

LOCATIONS = {
    "919000000001": (20.3559, 85.8195, "Patia Govt High School"),
    "919000000002": (20.3561, 85.8199, "Patia"),
    "919000000003": (20.3555, 85.8190, "Patia school"),
    "919000000004": (20.2380, 85.8340, "Old Town, Bhubaneswar"),
}


def _envelope(msg: dict) -> dict:
    """Wrap a message the way Meta wraps it, metadata and all."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "2251178702312944",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "15556634460",
                                 "phone_number_id": PHONE_ID},
                    "messages": [msg],
                },
            }],
        }],
    }


def build() -> list[dict]:
    out = []
    for i, (frm, kind, ts, body) in enumerate(MESSAGES, start=1):
        out.append({
            "id": f"wamid.SEED{i:03d}",
            "from": frm,
            "timestamp": str(ts),
            "type": kind,
            "text": {"body": body},
        })
        if frm in LOCATIONS:
            lat, lng, name = LOCATIONS[frm]
            out.append({
                "id": f"wamid.SEEDLOC{i:03d}",
                "from": frm,
                "timestamp": str(ts + 20),
                "type": "location",
                "location": {"latitude": lat, "longitude": lng, "name": name},
            })
    return out


def insert() -> None:
    init_db()
    added = 0
    with get_db() as conn:
        for msg in build():
            try:
                conn.execute(
                    "INSERT INTO raw_messages "
                    "(wa_message_id, from_number, kind, payload, received_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (msg["id"], msg["from"], msg["type"], json.dumps(msg),
                     datetime.now(timezone.utc).isoformat()),
                )
                added += 1
            except Exception:
                pass       # UNIQUE violation - already seeded, which is fine
    print(f"inserted {added} message(s)")


def show() -> None:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM raw_messages ORDER BY id").fetchall()
    if not rows:
        print("nothing in raw_messages - run `python seed.py` first")
        return

    # One message is never a whole report - people send the text and then the
    # pin separately. Merge each person's messages before judging completeness.
    per_message = [extract(json.loads(r["payload"])) for r in rows]
    needs = group_by_reporter(per_message)

    print(f"{len(rows)} messages -> {len(needs)} reports\n")
    for need in sorted(needs, key=lambda n: n.reporter):
        mark = "READY  " if need.is_actionable() else "INCOMPLETE "
        where = (f"{need.lat:.4f},{need.lng:.4f}" if need.has_location
                 else "no location")
        print(f"{mark}{need.reporter}  {where}  {need.place_text}")
        print(f"    said : {need.raw_text[:90]}")
        print(f"    got  : {need.summary()}")
        if not need.is_actionable():
            print(f"    ask  : {', '.join(need.missing())}")
        print()


def clear() -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM raw_messages")
    print("raw_messages emptied")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    {"--show": show, "--clear": clear}.get(arg, insert)()
