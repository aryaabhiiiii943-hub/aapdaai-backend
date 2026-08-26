"""Where reports meet incidents that already exist.

THE PROBLEM THIS SOLVES
    Before this, incidents were rebuilt from scratch on every request and
    numbered 1, 2, 3 as they came out. Add one new report, the clustering
    reorders, and yesterday's incident #2 is today's #3. Any decision stored
    against a number would silently point at the wrong emergency.

    So an incident, once it exists, has an identity. New reports attach to it.
    Only genuinely new locations create new incidents. That is also just how a
    control room works: you don't renumber an ongoing emergency because
    another call came in.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db import get_db
from app.incident import Incident, distance_m, RADIUS_M
from app.models import Need


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_incident(row) -> Incident:
    return Incident(
        id=row["id"],
        lat=row["lat"],
        lng=row["lng"],
        place_text=row["place_text"],
        confirmation=row["confirmation"],
        response=row["response"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def assign(needs: list[Need]) -> list[Incident]:
    """Attach reports to incidents, creating incidents only when needed.

    Three cases, in order:
      1. this report is already linked  -> put it back where it was
      2. it's near an existing incident -> attach, no new row
      3. neither                        -> a new incident is born
    """
    # ACTIONABLE, not merely located.
    # A pin with nothing said, or "we are fine here", would otherwise put a
    # red dot on the map with nothing behind it and send an operator to look
    # at an emergency nobody described.
    #
    # `cluster()` in incident.py enforces the same rule - but production goes
    # through THIS function, so fixing it there and not here left the real
    # path unchanged while the test went green. Keep them in step.
    located = [n for n in needs if n.is_actionable()]

    with get_db() as conn:
        incidents = {row["id"]: _row_to_incident(row)
                     for row in conn.execute("SELECT * FROM incidents")}
        links = {row["report_key"]: row["incident_id"]
                 for row in conn.execute(
                     "SELECT report_key, incident_id FROM incident_reports")}

        for need in sorted(located, key=lambda n: n.received_at):
            key = need.key

            # 1. already decided where this belongs
            existing = links.get(key)
            if existing in incidents:
                incidents[existing].add(need)
                need.incident_id = existing
                continue

            # 2. close enough to something we already know about
            match = _nearest(incidents.values(), need)
            if match is None:
                # 3. new emergency
                new_id = conn.insert_id(
                    "INSERT INTO incidents "
                    "(lat, lng, place_text, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (need.lat, need.lng, need.place_text,
                     need.received_at.isoformat(), _now()),
                )
                match = Incident(
                    id=new_id,
                    lat=need.lat, lng=need.lng,
                    place_text=need.place_text,
                    created_at=need.received_at,
                )
                incidents[match.id] = match

            match.add(need)
            need.incident_id = match.id
            links[key] = match.id
            conn.execute(
                "INSERT INTO incident_reports "
                "(report_key, incident_id, linked_at) VALUES (?, ?, ?) "
                "ON CONFLICT (report_key) DO UPDATE SET "
                "incident_id = excluded.incident_id, "
                "linked_at = excluded.linked_at",
                (key, match.id, _now()),
            )

        # The pin drifts toward wherever the reports actually cluster, so keep
        # the stored position in step. Decisions are untouched.
        for incident in incidents.values():
            if incident.needs:
                conn.execute(
                    "UPDATE incidents SET lat=?, lng=?, place_text=?, "
                    "updated_at=? WHERE id=?",
                    (incident.lat, incident.lng, incident.place_text,
                     _now(), incident.id),
                )

    return [i for i in incidents.values() if i.needs]


def _nearest(incidents, need: Need) -> Incident | None:
    """Closest incident within range, or None.

    Nearest rather than first-match: with several incidents in one
    neighbourhood, "the first one I happened to check" is not a defensible
    reason to merge two emergencies.
    """
    best, best_d = None, RADIUS_M
    for incident in incidents:
        d = distance_m(incident.lat, incident.lng, need.lat, need.lng)
        if d <= best_d:
            best, best_d = incident, d
    return best


# --- decisions ---------------------------------------------------------------

DECISIONS = ("confirmed", "rejected", "duplicate", "ground_check")

# What each decision means for whether we act. 'ground_check' deliberately
# leaves confirmation open: sending one person to look is not the same as
# believing the report.
_EFFECT = {
    "confirmed": "confirmed",
    "rejected": "rejected",
    "duplicate": "rejected",
    "ground_check": "verifying",
}


def verify(incident_id: int, decided_by: str, decision: str,
           note: str = "") -> dict:
    """A named human decides. Recorded, never overwritten."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")

    with get_db() as conn:
        row = conn.execute("SELECT id FROM incidents WHERE id=?",
                           (incident_id,)).fetchone()
        if row is None:
            raise LookupError(f"no incident {incident_id}")

        conn.execute(
            "INSERT INTO verifications "
            "(incident_id, decided_by, decision, note, decided_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (incident_id, decided_by, decision, note, _now()),
        )
        conn.execute(
            "UPDATE incidents SET confirmation=?, updated_at=? WHERE id=?",
            (_EFFECT[decision], _now(), incident_id),
        )
    return {"incident_id": incident_id, "confirmation": _EFFECT[decision],
            "decided_by": decided_by, "decision": decision}


RESPONSES = ("pending", "assigned", "in_progress", "resolved")


def set_response(incident_id: int, response: str) -> dict:
    """The other lifecycle: is anyone actually dealing with it."""
    if response not in RESPONSES:
        raise ValueError(f"response must be one of {RESPONSES}")
    with get_db() as conn:
        conn.execute(
            "UPDATE incidents SET response=?, updated_at=? WHERE id=?",
            (response, _now(), incident_id))
    return {"incident_id": incident_id, "response": response}


def history(incident_id: int) -> list[dict]:
    """Who decided what, when. The audit trail an authority is answerable to."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT decided_by, decision, note, decided_at FROM verifications "
            "WHERE incident_id=? ORDER BY id", (incident_id,)).fetchall()
    return [dict(r) for r in rows]
