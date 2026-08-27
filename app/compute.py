"""Turning an incident into two numbers and a shopping list.

THE RULE THIS FILE EXISTS TO ENFORCE
    People report what they can see. The system computes what that means.

    A man standing in floodwater can tell you there are 200 people and no
    drinking water. He cannot tell you that's 600 litres for the first day. So
    we never ask him - we ask him to count, and we do the arithmetic.

    That is also why it's defensible: every number below traces back to a
    published standard or to an assumption written down in this file, and none
    of it is a model's opinion.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.incident import Incident

# --- planning figures --------------------------------------------------------
# CITED. Sphere Handbook sets 15 L per person per day for drinking, cooking and
# hygiene in a camp setting.
WATER_LITRES_PER_PERSON_DAY = 15

# CITED (survival minimum). Drinking alone, for the first hours, is ~3 L. We
# plan the first response on this and the sustained figure above afterwards,
# because trucking 15 L a head in hour one is not achievable.
WATER_LITRES_DRINKING_DAY = 3

# OUR ASSUMPTIONS, not standards. Written here so a judge can challenge the
# number rather than the idea, and so you can change them in one place.
PEOPLE_PER_FOOD_PACK = 1          # one ration pack per person per day
PEOPLE_PER_TENT = 5
INJURED_PER_AMBULANCE = 4         # triage capacity of one vehicle per run
TRAPPED_PER_RESCUE_TEAM = 10

# What one district can plausibly field within a couple of hours, from its own
# resources, before asking the state for help. Rough, and meant to be argued
# with - the point is that SOME ceiling exists.
#
# WHY WE FLAG RATHER THAN CAP
#     A report of 200 people trapped really does imply 20 rescue teams. Capping
#     the number at 4 would quietly turn 200 trapped people into 40, and the
#     officer would never learn that the requirement is beyond them.
#
#     So the requirement stands, and we say plainly that it exceeds local
#     capacity and needs escalating. That is what escalation IS - and it is a
#     better answer than a number nobody can act on.
LOCAL_CAPACITY = {
    "rescue_teams": 4,
    "ambulances": 6,
    "tents": 200,
    "food_packs": 2000,
    "water_litres_now": 20_000,
}

# One tanker's worth. Used to work out how many trips the water alone needs -
# "540 litres" and "18,000 litres" are the same sentence and very different
# logistics.
LITRES_PER_TANKER = 5000


def resources_for(incident: Incident) -> dict[str, float | int]:
    """What to actually send. Empty dict if we don't know enough yet."""
    out: dict[str, float | int] = {}
    people = incident.headcount or 0
    deficits = incident.deficits

    if "water" in deficits and people:
        out["water_litres_now"] = people * WATER_LITRES_DRINKING_DAY
        out["water_litres_per_day"] = people * WATER_LITRES_PER_PERSON_DAY

    if "food" in deficits and people:
        out["food_packs"] = -(-people // PEOPLE_PER_FOOD_PACK)   # ceil

    if "shelter" in deficits and people:
        out["tents"] = -(-people // PEOPLE_PER_TENT)

    if incident.injured:
        out["ambulances"] = max(1, -(-incident.injured // INJURED_PER_AMBULANCE))
    elif "medical" in deficits:
        out["ambulances"] = 1

    if incident.trapped:
        out["rescue_teams"] = max(
            1, -(-incident.trapped // TRAPPED_PER_RESCUE_TEAM))
    elif "rescue" in deficits:
        out["rescue_teams"] = 1

    return out


def exceeds_local_capacity(resources: dict) -> list[str]:
    """Which requirements are beyond what a district can field itself.

    Returns the names, so the officer sees exactly what to escalate rather
    than a vague 'this is big'.
    """
    return [name for name, amount in resources.items()
            if name in LOCAL_CAPACITY and amount > LOCAL_CAPACITY[name]]


# --- how sure are we ---------------------------------------------------------

def confidence(incident: Incident) -> float:
    """0..1 - how much we believe this is real.

    Independent voices compound. If one anonymous reporter is right 30% of the
    time on their own, the chance that three independent people are ALL wrong
    about the same thing at the same place is 0.7^3. So:

        confidence = 1 - product(1 - trust of each distinct reporter)

    Two properties worth stating out loud:
      * it rises with INDEPENDENT sources, never with repetition - five
        messages from one phone is one voice
      * one official confirmation outweighs a dozen anonymous reports, which
        is the correct ordering
    """
    seen: dict[str, float] = {}
    for need in incident.needs:
        key = need.reporter or id(need)
        seen[key] = max(seen.get(key, 0.0), need.trust)

    doubt = 1.0
    for trust in seen.values():
        doubt *= (1.0 - trust)
    return round(1.0 - doubt, 3)


def confidence_label(value: float) -> str:
    if value >= 0.75:
        return "high"
    if value >= 0.45:
        return "medium"
    return "low"


# --- how bad is it -----------------------------------------------------------

# What kind of shortage this is, before any headcount. Rescue outranks
# everything: a trapped person has hours, a thirsty person has a day.
DEFICIT_WEIGHT = {"rescue": 40, "medical": 35, "water": 30,
                  "food": 20, "shelter": 20}

VULNERABLE_MAX = 15


def severity(incident: Incident, now: datetime | None = None) -> int:
    """0..100 - how bad this is IF TRUE. Never mixed with confidence.

    Keeping them apart is the whole point. A single anonymous report of a
    collapse with 40 trapped is severity 95, confidence 0.3: send someone to
    look, don't send everything. One number cannot say that.
    """
    now = now or datetime.now(timezone.utc)
    score = 0.0

    if incident.deficits:
        score += max(DEFICIT_WEIGHT.get(d, 10) for d in incident.deficits)

    people = incident.headcount or 0
    score += min(people / 50, 1.0) * 20

    if incident.injured:
        score += min(incident.injured / 10, 1.0) * 20
    if incident.trapped:
        score += min(incident.trapped / 10, 1.0) * 20

    # PEOPLE WHO CANNOT GET THEMSELVES OUT.
    # Not a sympathy weighting. A group that includes children, elderly,
    # pregnant women or people who cannot walk takes longer to move, needs more
    # hands, and cannot wait for the second wave. Same headcount, harder
    # rescue - so it goes higher in the queue.
    #
    # Capped at 15 so it sharpens the ordering without ever outweighing the
    # difference between a shortage of food and someone trapped under a slab.
    score += min(len(incident.vulnerable) * 6, VULNERABLE_MAX)

    # AGE MAKES IT WORSE, NOT BETTER.
    # The formula on the current site decays an incident toward zero over 20
    # hours, which says an unattended collapse becomes less urgent the longer
    # nobody goes. It's the wrong sign. Unmet need gets louder.
    if incident.response in ("pending", "assigned"):
        hours = max(0.0, (now - incident.created_at).total_seconds() / 3600)
        score += min(hours * 2, 15)

    return int(max(0, min(100, round(score))))


def band(value: int) -> str:
    if value >= 70:
        return "critical"
    if value >= 50:
        return "high"
    if value >= 30:
        return "medium"
    return "low"


# --- the thing an officer can act on -----------------------------------------

def _assistance(incident: Incident) -> dict:
    """Never let this raise - a brief with no assistance state is still useful,
    a dashboard that 500s is not."""
    try:
        from app import assistance
        return assistance.state_of(incident)
    except Exception:                             # noqa: BLE001
        return {"assistance": "unassisted", "asked": 0, "answered": 0,
                "confirmed_arrived": 0, "still_waiting": 0}


def brief(incident: Incident) -> dict:
    """Everything needed to make one decision, and nothing else.

    A priority number is not actionable. Where, what, how much, how sure, and
    why this one first - that is.
    """
    from app import resources as inventory

    sev = severity(incident)
    conf = confidence(incident)
    resources = resources_for(incident)
    beyond = exceeds_local_capacity(resources)

    # Which named units, and whether enough of them exist. "Send 2 ambulances"
    # is a requirement; "send Capital Hospital Ambulance 04, 4.2 km" is a
    # decision, and the difference is an inventory.
    units = inventory.recommend(incident)
    required = {u["kind"]: 1 for u in units}
    if resources.get("ambulances"):
        required["ambulance"] = int(resources["ambulances"])
    if resources.get("rescue_teams"):
        required["rescue_team"] = int(resources["rescue_teams"])
    if resources.get("water_litres_now"):
        required["supply_vehicle"] = max(
            1, -(-int(resources["water_litres_now"]) // LITRES_PER_TANKER))
    gaps = inventory.shortage(required)
    return {
        "id": incident.id,
        "place": incident.place_text or "unnamed location",
        "lat": round(incident.lat, 6),
        "lng": round(incident.lng, 6),
        "people": incident.headcount,
        "injured": incident.injured,
        "trapped": incident.trapped,
        "needs": incident.deficits,
        "vulnerable": incident.vulnerable,
        "photos": incident.photos,
        "reported_by": incident.reported_by,
        "hazard": incident.hazard,
        "access_blocked": incident.access_blocked,
        "send": resources,
        "dispatch": units,               # named units, nearest available
        "shortage": gaps,                # need minus what exists
        "assigned": inventory.assigned_to(incident.id) if incident.id else [],
        "exceeds_local_capacity": beyond,
        "severity": sev,
        "severity_band": band(sev),
        "confidence": conf,
        "confidence_label": confidence_label(conf),
        "reports": len(incident.needs),
        "independent_reporters": len(incident.reporters),
        "confirmation": incident.confirmation,
        "response": incident.response,
        # What the people there actually experienced, which is not the same
        # thing as what we dispatched.
        **_assistance(incident),
        "created_at": incident.created_at.isoformat(),
    }
