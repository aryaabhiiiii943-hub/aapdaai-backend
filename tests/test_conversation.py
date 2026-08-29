"""The follow-up conversation. Breaks quietly, which is the dangerous kind.

A question loop that nags, or one that goes silent when it doesn't understand,
loses the reporter - and a lost reporter is a report that never completes.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest


@pytest.fixture
def fresh(monkeypatch):
    """A throwaway database per test, so ordering can't hide a bug."""
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    monkeypatch.setattr("app.config.DATABASE_PATH", path)
    monkeypatch.setattr("app.db.DATABASE_PATH", path)
    from app.db import init_db
    init_db()

    sent: list[tuple[str, str]] = []

    def fake_send(to, body):
        sent.append((to, body))
        from app.send import SendResult
        return SendResult(True, message_id="fake")

    monkeypatch.setattr("app.send.send_text", fake_send)
    # notify imported send by module, so patch the attribute it actually uses
    monkeypatch.setattr("app.notify.send.send_text", fake_send)
    return sent


PHONE = "918935842629"
PATIA = (20.3559, 85.8195)


def inbound(text=None, location=False, mid=None):
    from app.db import get_db
    mid = mid or f"m{datetime.now(timezone.utc).timestamp()}"
    payload = ({"id": mid, "from": PHONE, "timestamp": "1700000000",
                "type": "location",
                "location": {"latitude": PATIA[0], "longitude": PATIA[1],
                             "name": "Patia"}}
               if location else
               {"id": mid, "from": PHONE, "timestamp": "1700000000",
                "type": "text", "text": {"body": text}})
    with get_db() as conn:
        conn.execute(
            "INSERT INTO raw_messages "
            "(wa_message_id, from_number, kind, payload, received_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (mid, PHONE, payload["type"], json.dumps(payload),
             datetime.now(timezone.utc).isoformat()))
    from app import pipeline
    return pipeline.respond_to(PHONE, text or "")


def test_it_asks_for_location_before_anything_else(fresh):
    """Nothing else matters until we can find them."""
    assert inbound("we are 200 hostelers stuck here") == "asked:location"


def test_it_does_not_ask_what_it_was_already_told(fresh):
    """They said 200. Asking 'how many people' wastes the one reply you get."""
    inbound("we are 200 hostelers stuck here", mid="a1")
    result = inbound(location=True, mid="a2")
    # Next question is about the hazard, not the headcount it already knows.
    assert result.startswith("asked:hazard")


def test_it_does_not_nag(fresh):
    """Asked three times, a person in a flood puts the phone down."""
    inbound("we are 200 hostelers stuck here", mid="b1")
    inbound(location=True, mid="b2")
    before = len(fresh)
    inbound("", mid="b3")               # nothing new said
    assert len(fresh) == before


def test_not_understanding_gets_a_reply_not_silence(fresh):
    """Answering and hearing nothing back means nobody is listening."""
    inbound("we are 200 hostelers stuck here", mid="c1")
    inbound(location=True, mid="c2")
    result = inbound("the whole building came down on us", mid="c3")
    assert result == "reasked"
    assert "didn't catch that" in fresh[-1][1]


def test_giving_up_moves_on_instead_of_dead_ending(fresh):
    """One unparseable answer must not block every question after it."""
    inbound("we are 200 hostelers stuck here", mid="d1")
    inbound(location=True, mid="d2")
    inbound("the whole building came down on us", mid="d3")   # re-asked
    result = inbound("sorry what do you mean", mid="d4")      # give up
    assert result.startswith("asked:")
    assert "hazard" not in result


def test_a_tapped_number_is_understood(fresh):
    inbound("we are 200 hostelers stuck here", mid="e1")
    inbound(location=True, mid="e2")
    result = inbound("2", mid="e3")          # Flood / water
    assert "learned" in result

    from app.pipeline import load_needs
    need = [n for n in load_needs() if n.reporter == PHONE][0]
    assert need.hazard == "flood"


def test_answers_survive_being_recomputed(fresh):
    """Reports are re-derived from raw messages, and '2' means nothing then.

    If answers weren't stored separately, every rebuild would forget them.
    """
    inbound("we are 200 hostelers stuck here", mid="f1")
    inbound(location=True, mid="f2")
    inbound("2", mid="f3")

    from app.pipeline import load_needs
    for _ in range(3):                        # rebuild repeatedly
        need = [n for n in load_needs() if n.reporter == PHONE][0]
        assert need.hazard == "flood"


def test_the_report_is_actionable_before_any_question_is_answered(fresh):
    """Questions sharpen an estimate. They are not a gate in front of help."""
    inbound("we are 200 hostelers stuck here", mid="g1")
    inbound(location=True, mid="g2")

    from app import pipeline
    incidents, _ = pipeline.build()
    assert len(incidents) == 1
    assert incidents[0].trapped == 200


def test_i_dont_know_moves_forward_and_concludes(fresh):
    """Unknown answers must not re-ask forever or leave the person in silence."""
    inbound("we are 4 people stuck here", mid="h1")
    inbound(location=True, mid="h2")

    result = inbound("I don't know", mid="h3")
    assert result == "asked:deficits (learned)"

    result = inbound("I don't know", mid="h4")
    assert result == "concluded"
    assert "recorded" in fresh[-1][1]
    assert "ask" in fresh[-1][1]
