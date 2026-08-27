"""Was this report actually assisted?

THE QUESTION THIS EXISTS TO ANSWER
    Of the 340 reports that came in on Tuesday, which ones did anyone reach?

    Nobody can answer that today. Calls are logged, units are tasked, and
    whether help arrived lives in a duty officer's memory. So a control room
    can tell you how many reports it received and how many units it sent, and
    not how many people were actually helped.

THE DISTINCTION THAT MATTERS
    `response` is what WE did - pending, assigned, in progress.
    `assistance` is what the PERSON THERE experienced.

    They are not the same, and conflating them is how a map turns green
    because a truck was dispatched. Only the people standing there know
    whether it arrived, so we ask them.

    This is also the only claim in the project that nobody else in the room
    will be making.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import get_db
from app.incident import Incident

# Long enough for a vehicle to plausibly arrive; short enough that a silent
# failure surfaces while it can still be fixed.
ASK_AFTER_MINUTES = 25

# States, in the order things go right.
UNASSISTED = "unassisted"     # nothing has been sent
ASSISTED = "assisted"         # a unit is committed, nobody has confirmed
ARRIVED = "arrived"           # someone there says it reached them
UNREACHABLE = "unreachable"   # someone there says it has not


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- asking -------------------------------------------------------------------

def ask_arrival(incident: Incident) -> int:
    """Message everyone who reported it: has help reached you?

    Everyone, not the first one. Four people reported the Patia flood; three of
    them being left in silence is how three people conclude nobody is coming.
    """
    from app import notify

    sent = 0
    with get_db() as conn:
        for reporter in incident.reporters:
            # Don't ask the same person twice for the same incident while an
            # earlier question is still unanswered.
            open_check = conn.execute(
                "SELECT 1 FROM arrival_checks WHERE incident_id = ? "
                "AND reporter = ? AND replied_at IS NULL",
                (incident.id, reporter)).fetchone()
            if open_check:
                continue
            conn.execute(
                "INSERT INTO arrival_checks (incident_id, reporter, asked_at) "
                "VALUES (?, ?, ?)", (incident.id, reporter, _now()))
            sent += 1

    if sent:
        notify.delivery_check(incident)
    return sent


def pending_for(reporter: str) -> int | None:
    """Which incident, if any, is this person owed an answer about."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT incident_id FROM arrival_checks WHERE reporter = ? "
            "AND replied_at IS NULL ORDER BY id DESC LIMIT 1",
            (reporter,)).fetchone()
    return row["incident_id"] if row else None


# --- their answer -------------------------------------------------------------

_YES = ("yes", "haan", "ha", "aa gaya", "aagaya", "arrived", "reached",
        "aa gaye", "pahunch", "received", "got it", "y")
_NO = ("no", "nahi", "nahin", "not yet", "nobody", "koi nahi", "still waiting",
       "nothing", "n")


def record_reply(reporter: str, text: str) -> str | None:
    """Read a YES/NO answer to the arrival question.

    Returns the state it resolved to, or None if this wasn't an answer.

    We accept words as well as a tapped reply, in the languages people
    actually use - someone who types "koi nahi aaya" has answered clearly and
    should not be asked again.
    """
    incident_id = pending_for(reporter)
    if incident_id is None:
        return None

    low = text.strip().lower()
    if not low:
        return None

    arrived = None
    if any(low.startswith(w) or f" {w}" in f" {low}" for w in _YES):
        arrived = True
    elif any(low.startswith(w) or f" {w}" in f" {low}" for w in _NO):
        arrived = False
    if arrived is None:
        return None

    with get_db() as conn:
        conn.execute(
            "UPDATE arrival_checks SET replied_at = ?, arrived = ?, note = ? "
            "WHERE incident_id = ? AND reporter = ? AND replied_at IS NULL",
            (_now(), arrived, text[:200], incident_id, reporter))

    print(f"[arrival] incident {incident_id}: {reporter} says "
          f"{'ARRIVED' if arrived else 'STILL WAITING'}")
    return ARRIVED if arrived else UNREACHABLE


# --- reading the state --------------------------------------------------------

def state_of(incident: Incident) -> dict:
    """What actually happened to this report, from the ground's point of view."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT arrived, replied_at FROM arrival_checks "
            "WHERE incident_id = ?", (incident.id,)).fetchall()
        committed = conn.execute(
            "SELECT COUNT(*) AS n FROM assignments "
            "WHERE incident_id = ? AND released_at IS NULL",
            (incident.id,)).fetchone()["n"]

    # SQLite hands booleans back as 0/1, Postgres as True/False. `is False`
    # silently fails on the integer, so the "no" was recorded and never
    # counted - which would have made the map turn green on a report where
    # someone had just said nobody had come.
    answered = [r for r in rows if r["replied_at"]]
    said_yes = [r for r in answered if bool(r["arrived"])]
    said_no = [r for r in answered if not bool(r["arrived"])]

    if said_yes:
        state = ARRIVED
    elif said_no:
        # Somebody on the ground says nothing has reached them. That outranks
        # anything the dispatch record claims.
        state = UNREACHABLE
    elif committed:
        state = ASSISTED
    else:
        state = UNASSISTED

    return {
        "assistance": state,
        "asked": len(rows),
        "answered": len(answered),
        "confirmed_arrived": len(said_yes),
        "still_waiting": len(said_no),
    }


def unassisted(incidents: list[Incident]) -> list[dict]:
    """Reports nobody has reached yet - oldest first.

    THE MOST IMPORTANT NUMBER ON THE DASHBOARD, and the one a control room
    cannot produce today. Sorted by age rather than severity on purpose: a
    small emergency nobody has touched for three hours is a worse failure than
    a large one being actively worked.
    """
    out = []
    now = datetime.now(timezone.utc)
    for incident in incidents:
        s = state_of(incident)
        if s["assistance"] in (UNASSISTED, UNREACHABLE):
            waiting = (now - incident.created_at).total_seconds() / 60
            out.append({
                "id": incident.id,
                "place": incident.place_text,
                "assistance": s["assistance"],
                "waiting_minutes": int(waiting),
                "people": incident.headcount,
                "severity_hint": len(incident.deficits),
            })
    out.sort(key=lambda r: -r["waiting_minutes"])
    return out


def due_for_check(incidents: list[Incident]) -> list[Incident]:
    """Assigned a while ago, nobody asked yet whether it arrived."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ASK_AFTER_MINUTES)
    due = []
    with get_db() as conn:
        for incident in incidents:
            row = conn.execute(
                "SELECT MIN(assigned_at) AS first FROM assignments "
                "WHERE incident_id = ? AND released_at IS NULL",
                (incident.id,)).fetchone()
            if not row or not row["first"]:
                continue
            if datetime.fromisoformat(row["first"]) > cutoff:
                continue
            asked = conn.execute(
                "SELECT COUNT(*) AS n FROM arrival_checks WHERE incident_id = ?",
                (incident.id,)).fetchone()["n"]
            if not asked:
                due.append(incident)
    return due
