"""raw messages -> reports -> incidents.

    read  ->  extract  ->  merge per person  ->  enrich  ->  cluster  ->  score

WHY THIS RECOMPUTES FROM SCRATCH
    Incidents are derived, not stored. Every call rebuilds them from
    raw_messages, which at this scale costs milliseconds and removes a whole
    category of bug: there is no cached incident that can drift out of step
    with the reports underneath it.

    It stops being the right choice somewhere in the thousands of messages.
    The fix then is to cache the result, not to mutate incidents in place -
    the raw table stays the single source of truth either way.
"""
from __future__ import annotations

import json

from app import store
from app.compute import brief
from app.db import get_db
from app.extract import enrich, extract, group_by_reporter
from app.incident import Incident, unlocatable
from app.models import Need


def load_needs(use_llm: bool = False) -> list[Need]:
    """Every stored message, read and folded into one report per person."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT payload FROM raw_messages ORDER BY id").fetchall()

    per_message = [extract(json.loads(r["payload"])) for r in rows]
    needs = group_by_reporter(per_message)

    if use_llm:
        # Only the reports the parser left holes in - enrich() checks that
        # itself, so this is a handful of calls rather than one per report.
        needs = [enrich(n) for n in needs]
    return needs


def build(use_llm: bool = False) -> tuple[list[Incident], list[Need]]:
    """The whole pipeline. Returns (incidents, reports we couldn't place).

    Incidents come from `store.assign`, not from clustering in memory: once an
    incident exists it keeps its identity, so a decision recorded against it
    still means the same thing tomorrow. Its numbers are still derived from
    the reports attached to it.
    """
    needs = load_needs(use_llm=use_llm)
    incidents = store.assign(needs)
    return incidents, unlocatable(needs)


def briefs(use_llm: bool = False) -> list[dict]:
    """What the dashboard asks for: ranked, actionable, worst first."""
    incidents, _ = build(use_llm=use_llm)
    out = [brief(i) for i in incidents]
    out.sort(key=lambda b: (b["severity"], b["confidence"]), reverse=True)
    return out


def follow_ups(use_llm: bool = False) -> list[dict]:
    """Reports that cannot be acted on yet, and what to ask each person.

    These are not failures. A message saying only "help" is a person who needs
    one question answered, not a record to discard.
    """
    _, stuck = build(use_llm=use_llm)
    return [{
        "reporter": n.reporter,
        "said": n.raw_text[:200],
        "missing": n.missing(),
        "ask": _question_for(n.missing()),
    } for n in stuck]


def respond_to(reporter: str) -> str:
    """Decide what to say back to one person, and say it.

    Runs after every inbound message. Two outcomes:

      * we still can't act on their report -> ask for the ONE most blocking
        thing, and remember that we asked
      * we now can -> tell them it's been passed on

    The remembering matters. Without it, every message they send triggers the
    same question again, and a person in a flood being asked four times for
    their location will stop replying - which is the one outcome we cannot
    afford.
    """
    from app import notify

    needs = [n for n in load_needs() if n.reporter == reporter]
    if not needs:
        return ""
    need = max(needs, key=lambda n: n.received_at)

    if need.is_actionable():
        if _remember(reporter, "acknowledged"):
            notify.acknowledge(need)
            return "acknowledged"
        return ""

    slot = next((s for s in ("location", "headcount", "deficits")
                 if s in need.missing()), "")
    if not slot:
        return ""
    if not _remember(reporter, f"asked:{slot}"):
        return ""                      # already asked this, don't nag
    notify.ask_follow_up(need)
    return f"asked:{slot}"


def _remember(phone: str, state: str) -> bool:
    """Record what we last said to someone. True if this is new.

    Survives a restart, unlike the dict the restaurant bot used - which is the
    bug that lost a customer's order on a cold start.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        row = conn.execute("SELECT state FROM conversations WHERE phone=?",
                           (phone,)).fetchone()
        if row and row["state"] == state:
            return False
        conn.execute(
            "INSERT INTO conversations (phone, state, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(phone) DO UPDATE SET "
            "state=excluded.state, updated_at=excluded.updated_at",
            (phone, state, now))
    return True


_QUESTIONS = {
    "location": "Please share your location - tap the attachment icon, then Location.",
    "headcount": "Roughly how many people are with you?",
    "deficits": "What do you need most - water, food, medical help, shelter, or rescue?",
}


def _question_for(missing: list[str]) -> str:
    """One question at a time. A person in a flood will not fill in a form."""
    for slot in ("location", "headcount", "deficits"):
        if slot in missing:
            return _QUESTIONS[slot]
    return ""
