"""The three messages this system sends.

    1. to the officer     here is what to send, where, and how sure we are
    2. to the reporter    the one fact we still need from you
    3. to the reporter    did help actually reach you?

The third is the one almost nobody builds, and it is the one that makes the
rest honest. Without it your map turns green because something was SENT, not
because it ARRIVED.
"""
from __future__ import annotations

from app import ask, send, store
from app.compute import (band, brief, confidence, confidence_label, severity)
from app.config import OFFICER_NUMBER
from app.incident import Incident
from app.models import Need

# What a shortage translates to in plain words for a person reading a phone
# in a hurry.
_LABEL = {
    "water": "drinking water",
    "food": "food",
    "medical": "medical help",
    "shelter": "shelter",
    "rescue": "rescue",
}

_UNITS = {
    "water_litres_now": ("L drinking water (today)", ""),
    "water_litres_per_day": ("L/day sustained", ""),
    "food_packs": ("food packs", ""),
    "tents": ("tents", ""),
    "ambulances": ("ambulance(s)", ""),
    "rescue_teams": ("rescue team(s)", ""),
}


def officer_message(incident: Incident) -> str:
    """Six lines: where, what, how much, how sure, access, why this one.

    A priority number is not actionable. This is. Deliberately short enough to
    read on a lock screen, because that is where it will be read.
    """
    b = brief(incident)
    sev, conf = b["severity"], b["confidence"]

    lines = [f"*{b['place']}* — {band(sev).upper()}"]

    facts = []
    if b["people"]:
        facts.append(f"{b['people']} people")
    if b["injured"]:
        facts.append(f"{b['injured']} injured")
    if b["trapped"]:
        facts.append(f"{b['trapped']} trapped")
    # "no drinking water" reads correctly; "no rescue" does not. Shortages of
    # supplies are things people lack; rescue and medical help are things they
    # need. Small wording, but an officer reads this on a lock screen.
    lacking = [d for d in b["needs"] if d in ("water", "food", "shelter")]
    needing = [d for d in b["needs"] if d in ("rescue", "medical")]
    if lacking:
        facts.append("no " + ", ".join(_LABEL[d] for d in lacking))
    if needing:
        facts.append("needs " + ", ".join(_LABEL[d] for d in needing))
    if facts:
        lines.append(" · ".join(facts))

    # Put this on its own line. Buried in a list of facts an officer scanning a
    # lock screen will miss it, and it changes how many hands are needed.
    if b.get("vulnerable"):
        pretty = {"children": "children", "elderly": "elderly",
                  "pregnant": "pregnant woman", "disabled": "unable to walk"}
        lines.append("⚠ CANNOT SELF-EVACUATE: "
                     + ", ".join(pretty.get(v, v) for v in b["vulnerable"]))

    if b["send"]:
        parts = [f"{int(v)} {_UNITS.get(k, (k, ''))[0]}"
                 for k, v in b["send"].items()]
        lines.append("SEND: " + ", ".join(parts))

    # The whole reason for asking "what has happened". 200 trapped by water and
    # 200 trapped under a slab get the same headline number and completely
    # different equipment - and only the hazard tells you which.
    note = ask.hazard_note(b.get("hazard", ""))
    if note:
        lines.append(note)
    if b.get("access_blocked"):
        lines.append("Road access reported BLOCKED — plan an alternative approach.")

    # A named unit at a known distance is a decision. "2 ambulances" is a
    # requirement someone still has to turn into one.
    for d in b.get("dispatch", []):
        label = d["kind"].replace("_", " ")
        if d["available"]:
            lines.append(f"→ {label}: {d['unit']} ({d['org']}, {d['distance_km']} km)")
        else:
            lines.append(f"→ {label}: NONE AVAILABLE in district")

    # The gap, stated as a number. This is the difference between "we need
    # more" and "we are sixteen teams short".
    for gap in b.get("shortage", []):
        lines.append(f"SHORT {gap['shortage']} {gap['kind'].replace('_',' ')}"
                     f" (need {gap['required']}, have {gap['available']})")

    # Say it plainly rather than letting an officer read "20 rescue teams" and
    # quietly conclude the system doesn't understand its own domain.
    if b["exceeds_local_capacity"]:
        lines.append("⚠ BEYOND DISTRICT CAPACITY — escalate to state: "
                     + ", ".join(n.replace("_", " ")
                                 for n in b["exceeds_local_capacity"]))

    lines.append(
        f"Confidence: {confidence_label(conf)} "
        f"({b['independent_reporters']} independent report"
        f"{'s' if b['independent_reporters'] != 1 else ''})")

    lines.append(f"Map: https://maps.google.com/?q={b['lat']},{b['lng']}")

    # The ask, and it is never "we have dispatched". We recommend; they decide.
    if conf < 0.45:
        lines.append("Unconfirmed — recommend a ground check before dispatch.")
    else:
        lines.append(f"Confirm or reject: incident #{b['id']}")

    return "\n".join(lines)


def notify_officer(incident: Incident, to: str = "") -> send.SendResult:
    """Push one recommendation to a named officer.

    We do not dispatch. This is the fastest possible route from 'someone
    reported it' to 'the person with the authority knows', and that gap - not
    the dispatch - is what costs hours in a real disaster.
    """
    number = to or OFFICER_NUMBER
    if not number:
        return send.SendResult(False, error="no OFFICER_NUMBER configured")
    result = send.send_text(number, officer_message(incident))
    if result.ok:
        store.set_response(incident.id, "assigned")
    return result


# --- back to the person who reported ----------------------------------------

def follow_up_message(need: Need) -> str:
    """One question. Never a form.

    Someone standing in floodwater will answer one question. They will abandon
    five. So we ask for the most blocking gap and stop.
    """
    missing = need.missing()
    if "location" in missing:
        return ("We've received your message. To send help we need your "
                "location — tap the attachment icon (📎), choose *Location*, "
                "then *Send your current location*.")
    if "headcount" in missing:
        return "Thank you. Roughly how many people are with you right now?"
    if "deficits" in missing:
        return ("What do you need most? Reply with one: *water*, *food*, "
                "*medical*, *shelter* or *rescue*.")
    return ""


def ask_follow_up(need: Need) -> send.SendResult:
    body = follow_up_message(need)
    if not body:
        return send.SendResult(False, error="nothing missing")
    return send.send_text(need.reporter, body)


def acknowledge(need: Need) -> send.SendResult:
    """Tell them we have it. Silence is what makes people call 112 instead."""
    return send.send_text(
        need.reporter,
        "Your report has been received and passed to the district disaster "
        "authority. We will message you when help is on the way.")


def delivery_check(incident: Incident) -> list[send.SendResult]:
    """Ask everyone who reported it whether help actually arrived.

    This is the only honest way to mark a need resolved. 'We sent something'
    is not the same as 'it got there', and only the people standing there know
    which one is true.
    """
    body = ("Help was dispatched to your location. Has it reached you? "
            "Reply *YES* if it has, or *NO* if you are still waiting.")
    return [send.send_text(reporter, body)
            for reporter in incident.reporters]
