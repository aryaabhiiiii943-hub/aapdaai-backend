"""Who to send, and whether there are enough of them.

THE QUESTION THIS FILE ANSWERS
    "180 people need water" is a need. "Send Capital Hospital Ambulance 04,
    4.2 km away" is a decision. The gap between those two is an inventory.

THE PART THAT MATTERS MOST
    Once a resource is assigned it stops being available. Without that, two
    critical incidents both get recommended the same ambulance and nobody
    notices until neither is served. Nearest-available is only meaningful if
    "available" is maintained.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db import get_db
from app.incident import Incident, distance_m

KINDS = ("ambulance", "rescue_team", "fire_truck", "boat", "heavy_rescue",
         "supply_vehicle")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- what this emergency actually needs --------------------------------------

# THE FIX FOR "EVERYTHING SUGGESTS AMBULANCE".
# A chemical explosion, a flooded hostel and a collapsed slab are not the same
# dispatch. The hazard decides the equipment; the deficits and casualty counts
# decide how much.
_BY_HAZARD = {
    "flood":      ["boat", "rescue_team"],
    "collapse":   ["heavy_rescue", "ambulance"],
    "earthquake": ["heavy_rescue", "ambulance"],
    "fire":       ["fire_truck", "ambulance"],
    "storm":      ["rescue_team"],
}


def kinds_needed(incident: Incident) -> list[str]:
    """Which kinds of unit this incident calls for, most specific first."""
    wanted: list[str] = []

    for kind in _BY_HAZARD.get(incident.hazard, []):
        wanted.append(kind)

    # Casualties always need transport, whatever caused them.
    if incident.injured or "medical" in incident.deficits:
        if "ambulance" not in wanted:
            wanted.append("ambulance")

    # Someone is stuck and we were never told by what. Generic rescue, and the
    # follow-up question exists precisely to avoid landing here.
    if (incident.trapped or "rescue" in incident.deficits) and not wanted:
        wanted.append("rescue_team")

    # Somebody has to actually carry 540 litres of water there. Without this
    # the brief says what to send and names nobody to bring it - which is a
    # requirement, not a dispatch.
    if any(d in incident.deficits for d in ("water", "food", "shelter")):
        wanted.append("supply_vehicle")

    return wanted


# --- the inventory -----------------------------------------------------------

def all_resources(kind: str = "", status: str = "") -> list[dict]:
    sql = "SELECT * FROM resources"
    where, params = [], []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if status:
        where.append("status = ?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    with get_db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def facilities(kind: str = "") -> list[dict]:
    sql = "SELECT * FROM facilities"
    params: list = []
    if kind:
        sql += " WHERE kind = ?"
        params.append(kind)
    with get_db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def road_blocks() -> list[dict]:
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM road_blocks WHERE active = TRUE").fetchall()]


def counts() -> dict:
    """The available/deployed tiles."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM resources GROUP BY status"
        ).fetchall()
    out = {r["status"]: r["n"] for r in rows}
    return {"available": out.get("available", 0),
            "deployed": out.get("deployed", 0),
            "offline": out.get("offline", 0),
            "total": sum(out.values())}


# --- matching ----------------------------------------------------------------

def nearest_available(kind: str, lat: float, lng: float) -> dict | None:
    """Closest free unit of this kind.

    Straight-line distance, and we say so. Real road distance needs routing we
    don't have, and quoting a road figure we didn't compute would be worse than
    quoting an honest crow-flies one.
    """
    candidates = all_resources(kind=kind, status="available")
    if not candidates:
        return None
    for c in candidates:
        c["distance_km"] = round(
            distance_m(lat, lng, c["lat"], c["lng"]) / 1000, 1)
    return min(candidates, key=lambda c: c["distance_km"])


def recommend(incident: Incident) -> list[dict]:
    """One named unit per kind of help this incident needs.

    Returns what is actually available. When nothing of a kind is free that is
    itself the answer - and a more useful one than silence.
    """
    out = []
    for kind in kinds_needed(incident):
        unit = nearest_available(kind, incident.lat, incident.lng)
        out.append({
            "kind": kind,
            "unit": unit["name"] if unit else None,
            "resource_id": unit["id"] if unit else None,
            "org": unit["org"] if unit else "",
            "distance_km": unit["distance_km"] if unit else None,
            "available": unit is not None,
        })
    return out


def nearest_facility(kind: str, lat: float, lng: float) -> dict | None:
    """Closest hospital or shelter with room left."""
    options = [f for f in facilities(kind)
               if f["capacity"] == 0 or f["occupancy"] < f["capacity"]]
    if not options:
        return None
    for f in options:
        f["distance_km"] = round(distance_m(lat, lng, f["lat"], f["lng"]) / 1000, 1)
    return min(options, key=lambda f: f["distance_km"])


# --- shortage ----------------------------------------------------------------

def shortage(required: dict[str, int]) -> list[dict]:
    """Need minus stock, per kind. The strategy document's headline claim.

    "Zone B needs 40, stock is 10, shortage is 30" is only sayable if stock is
    a real number somewhere. This is that number.
    """
    out = []
    for kind, need in required.items():
        if kind not in KINDS:
            continue
        have = len(all_resources(kind=kind, status="available"))
        if need > have:
            out.append({"kind": kind, "required": need, "available": have,
                        "shortage": need - have})
    return out


# --- committing --------------------------------------------------------------

def assign(incident_id: int, resource_id: int,
           purpose: str = "response") -> dict:
    """Commit a unit to an incident, and take it out of the available pool.

    The status change is the whole point. A recommendation that doesn't remove
    the vehicle from circulation is a suggestion, not an allocation.
    """
    with get_db() as conn:
        row = conn.execute("SELECT * FROM resources WHERE id = ?",
                           (resource_id,)).fetchone()
        if row is None:
            raise LookupError(f"no resource {resource_id}")
        if row["status"] != "available":
            raise ValueError(f"{row['name']} is already {row['status']}")

        conn.execute(
            "INSERT INTO assignments "
            "(incident_id, resource_id, purpose, assigned_at) "
            "VALUES (?, ?, ?, ?)",
            (incident_id, resource_id, purpose, _now()))
        conn.execute(
            "UPDATE resources SET status = 'deployed', updated_at = ? "
            "WHERE id = ?", (_now(), resource_id))

    return {"incident_id": incident_id, "resource_id": resource_id,
            "unit": row["name"], "purpose": purpose, "status": "assigned"}


def release(resource_id: int) -> dict:
    """Job done - back into the pool."""
    with get_db() as conn:
        conn.execute(
            "UPDATE assignments SET status = 'released', released_at = ? "
            "WHERE resource_id = ? AND released_at IS NULL",
            (_now(), resource_id))
        conn.execute(
            "UPDATE resources SET status = 'available', updated_at = ? "
            "WHERE id = ?", (_now(), resource_id))
    return {"resource_id": resource_id, "status": "available"}


def assigned_to(incident_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT a.id, a.purpose, a.status, a.assigned_at, "
            "       r.name, r.kind, r.org "
            "FROM assignments a JOIN resources r ON r.id = a.resource_id "
            "WHERE a.incident_id = ? AND a.released_at IS NULL",
            (incident_id,)).fetchall()
    return [dict(r) for r in rows]
