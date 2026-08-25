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

def brief(incident: Incident) -> dict:
    """Everything needed to make one decision, and nothing else.

    A priority number is not actionable. Where, what, how much, how sure, and
    why this one first - that is.
    """
    sev = severity(incident)
    conf = confidence(incident)
    return {
        "id": incident.id,
        "place": incident.place_text or "unnamed location",
        "lat": round(incident.lat, 6),
        "lng": round(incident.lng, 6),
        "people": incident.headcount,
        "injured": incident.injured,
        "trapped": incident.trapped,
        "needs": incident.deficits,
        "send": resources_for(incident),
        "severity": sev,
        "severity_band": band(sev),
        "confidence": conf,
        "confidence_label": confidence_label(conf),
        "reports": len(incident.needs),
        "independent_reporters": len(incident.reporters),
        "confirmation": incident.confirmation,
        "response": incident.response,
        "created_at": incident.created_at.isoformat(),
    }
