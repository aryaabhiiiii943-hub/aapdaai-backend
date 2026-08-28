"""A question asked and never answered must not break every later message.

REGRESSION. Seen in production on 27 Aug:

    [recv] text from 9189358xxxxx
    [reply] TypeError: 'NoneType' object is not iterable

Both the give-up path and the answered-by-another-route path record a slot with
the value None, meaning "we already put this to them, don't ask again". That is
a MARKER, not an answer. load_needs folded it in anyway, and `for d in value`
raised on it.

WHY IT WAS INVISIBLE
    respond_to's caller catches and logs exceptions, so nothing crashed and
    nothing alerted. The person simply got no reply - and because the marker is
    stored, no reply ever again from that number. One unanswered question
    bricked the whole conversation, silently.
"""
import datetime
import json
import os
import tempfile
import uuid

import pytest

from app.db import get_db
from app import pipeline


@pytest.fixture
def fresh(monkeypatch):
    """A throwaway database per test, so ordering can't hide a bug."""
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    monkeypatch.setattr("app.config.DATABASE_PATH", path)
    monkeypatch.setattr("app.db.DATABASE_PATH", path)
    from app.db import init_db
    init_db()
    return path


def _store(phone: str, text: str) -> None:
    """One inbound WhatsApp text, stored the way the webhook stores it."""
    wamid = f"wamid.{uuid.uuid4().hex}"
    payload = {
        "from": phone,
        "id": wamid,
        "type": "text",
        "text": {"body": text},
        "timestamp": str(int(datetime.datetime.now().timestamp())),
    }
    with get_db() as conn:
        conn.execute(
            "INSERT INTO raw_messages "
            "(wa_message_id, from_number, kind, payload, received_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (wamid, phone, "text", json.dumps(payload),
             datetime.datetime.now(datetime.timezone.utc).isoformat()),
        )


def test_unanswered_question_does_not_break_later_messages(fresh):
    phone = f"9199{uuid.uuid4().int % 10**8:08d}"
    _store(phone, "we are 30 people stuck near patia")

    # Exactly what the give-up path writes after two unparseable replies.
    pipeline._save_conversation(
        phone, {"answers": {"deficits": None}, "pending": "", "misses": 0}
    )

    try:
        needs = [n for n in pipeline.load_needs() if n.reporter == phone]
    except TypeError as exc:                                   # noqa: PT017
        raise AssertionError(
            f"a None answer-marker crashed load_needs: {exc}"
        ) from exc

    assert needs, "the report itself must survive an unanswered question"

    # The marker must be ignored, not merged - and what they actually said
    # must still be there.
    assert "rescue" in needs[0].deficits, needs[0].deficits
    assert needs[0].headcount == 30


def test_none_marker_never_overwrites_a_known_value(fresh):
    """The same marker on a scalar slot must not blank out real information."""
    phone = f"9199{uuid.uuid4().int % 10**8:08d}"
    _store(phone, "40 log fanse hain, 5 ghayal")

    pipeline._save_conversation(
        phone, {"answers": {"injured": None, "hazard": None}, "pending": ""}
    )

    needs = [n for n in pipeline.load_needs() if n.reporter == phone]
    assert needs
    assert needs[0].injured == 5, "a marker must not erase what they told us"
