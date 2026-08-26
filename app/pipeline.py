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

    # Answers to our own questions can't be recovered by re-reading messages -
    # "2" means nothing without knowing what was asked. They're stored when
    # given and folded back in here.
    for need in needs:
        for slot, value in _conversation(need.reporter).get("answers", {}).items():
            if slot == "deficits":
                for d in value:
                    if d not in need.deficits:
                        need.deficits.append(d)
            elif getattr(need, slot, None) in (None, "", False) or slot == "hazard":
                setattr(need, slot, value)
    return needs


# --- what we've said to each person, and what they answered ------------------

def _slot_satisfied(need, slot: str) -> bool:
    """Do we now know this, however it reached us?"""
    return {
        "location": need.has_location,
        "headcount": need.headcount is not None,
        "injured": need.injured is not None,
        "deficits": bool(need.deficits),
        "hazard": bool(need.hazard),
        "vulnerable": bool(need.vulnerable),
        "access_blocked": need.access_blocked is not None,
    }.get(slot, False)


def _conversation(phone: str) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT state FROM conversations WHERE phone=?",
                           (phone,)).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["state"])
    except ValueError:
        return {}


def _save_conversation(phone: str, state: dict) -> None:
    from datetime import datetime, timezone
    with get_db() as conn:
        conn.execute(
            "INSERT INTO conversations (phone, state, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(phone) DO UPDATE SET "
            "state=excluded.state, updated_at=excluded.updated_at",
            (phone, json.dumps(state),
             datetime.now(timezone.utc).isoformat()))


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


def respond_to(reporter: str, last_text: str = "") -> str:
    """Read their latest message, learn from it, and ask the next thing.

    Order matters:

      1. if we asked them something, try to read this message as the answer
      2. acknowledge the first time their report becomes usable
      3. ask the single most valuable thing we still don't know

    Step 3 never blocks step 2. The report is already on the map and already
    reaching an officer; questions only sharpen the estimate. Someone who
    stops replying is not someone we stop helping.
    """
    from app import ask, notify
    from app.models import Need

    state = _conversation(reporter)
    answers = state.get("answers", {})
    pending = state.get("pending", "")
    learned = False

    needs = [n for n in load_needs() if n.reporter == reporter]
    if not needs:
        return ""
    need = max(needs, key=lambda n: n.received_at)

    # 0. DID THEY ANSWER IT WITHOUT REPLYING TO IT?
    # A shared location pin answers "where are you" perfectly, and arrives as a
    # location message with no text at all. Treating that as a failure to
    # answer - which it was, before this - made the system apologise for not
    # understanding, ask again, and then give up on a question the person had
    # already answered correctly.
    #
    # Pending clears when the information arrives by ANY route.
    if pending and _slot_satisfied(need, pending):
        state["pending"] = ""
        state["misses"] = 0
        answers.setdefault(pending, None)
        state["answers"] = answers
        _save_conversation(reporter, state)
        pending = ""
        learned = True

    # 1. Was this an answer to our last question?
    elif pending and last_text.strip():
        probe = Need(reporter=reporter)
        if ask.apply_answer(probe, pending, last_text):
            value = (probe.deficits if pending == "deficits"
                     else getattr(probe, pending))
            answers[pending] = value
            state["answers"] = answers
            state["pending"] = ""
            state["misses"] = 0
            _save_conversation(reporter, state)
            learned = True
            print(f"[ask] {reporter} answered {pending} = {value!r}")
        else:
            # WE DID NOT UNDERSTAND THEM.
            # Saying nothing back is the worst option available: they answered,
            # they got silence, and they conclude nobody is listening. Say so
            # once and re-offer the options - then stop, because a person in a
            # flood asked the same thing three times will put the phone down.
            misses = state.get("misses", 0) + 1
            state["misses"] = misses
            question = ask._question_for_slot(pending)
            if misses == 1 and question:
                notify.send.send_text(
                    reporter,
                    "Sorry, I didn't catch that.\n\n" + question.render())
                _save_conversation(reporter, state)
                return "reasked"
            # Twice is enough. Drop the question and carry on with what we have.
            print(f"[ask] giving up on {pending} for {reporter}")
            state["pending"] = ""
            state["misses"] = 0
            answers[pending] = answers.get(pending)   # mark as asked, unknown
            state["answers"] = answers
            _save_conversation(reporter, state)

    # The report may have changed while we were reading the answer.
    needs = [n for n in load_needs() if n.reporter == reporter]
    need = max(needs, key=lambda n: n.received_at) if needs else need

    # 2. Tell them it's been passed on - once, the first time it's usable.
    if need.is_actionable() and not state.get("acknowledged"):
        notify.acknowledge(need)
        state["acknowledged"] = True
        _save_conversation(reporter, state)

    # ONE QUESTION OUTSTANDING AT A TIME.
    # If we've asked something and they haven't answered it, sending a
    # different question isn't patience - it's two unanswered questions, which
    # is the nagging this was supposed to avoid. Wait. The give-up path clears
    # `pending`, so a question they genuinely can't answer still doesn't
    # dead-end the conversation.
    if state.get("pending"):
        return "waiting"

    # 3. The next question. Anything already put to them - answered or given up
    # on - is off the list, so one unparseable reply doesn't block the rest.
    question = ask.next_question(need, asked=set(answers.keys()))
    if question is None:
        return "nothing left to ask"

    notify.send.send_text(reporter, question.render())
    state["pending"] = question.slot
    _save_conversation(reporter, state)
    return f"asked:{question.slot}" + (" (learned)" if learned else "")


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
