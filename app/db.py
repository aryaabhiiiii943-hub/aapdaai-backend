"""SQLite for now. Postgres on deploy day - the SQL below is deliberately plain
so the move is a change of driver, not a rewrite.

Only two tables exist yet, and both are about *receiving*. Nothing here
interprets anything - that is the next layer's job.
"""
import sqlite3
from contextlib import contextmanager

from app.config import DATABASE_PATH

SCHEMA = """
-- Every message exactly as WhatsApp sent it, before we understand any of it.
-- If our parser turns out to be wrong at 2am on the 28th, this is what saves us.
CREATE TABLE IF NOT EXISTS raw_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_message_id TEXT UNIQUE NOT NULL,   -- UNIQUE is the dedup. Let the DB do it.
    from_number  TEXT NOT NULL,
    kind         TEXT NOT NULL,           -- text | location | image | audio | ...
    payload      TEXT NOT NULL,           -- the whole JSON, untouched
    received_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_from ON raw_messages(from_number);

-- One row per phone number we are mid-conversation with. Survives a restart,
-- which the restaurant bot's version did not.
CREATE TABLE IF NOT EXISTS conversations (
    phone      TEXT PRIMARY KEY,
    state      TEXT NOT NULL,             -- JSON: the slots filled so far
    updated_at TEXT NOT NULL
);

-- WHAT IS STORED HERE AND WHAT ISN'T
--
--   Stored     : identity and decisions. An incident's id, where it is, and
--                what a human decided about it. None of that can be
--                recalculated, and losing it loses the human's work.
--
--   Not stored : headcount, severity, confidence, what to send. Those are
--                pure functions of the reports attached to the incident, so
--                they are computed on read. A stored copy could disagree with
--                the reports underneath it, and then which one is true?
--
-- Persist what was decided. Derive what can be derived.
CREATE TABLE IF NOT EXISTS incidents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lat          REAL NOT NULL,
    lng          REAL NOT NULL,
    place_text   TEXT NOT NULL DEFAULT '',
    confirmation TEXT NOT NULL DEFAULT 'unconfirmed',
    response     TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- Which report belongs to which incident. `report_key` is stable across
-- rebuilds (see Need.key), so re-deriving the reports never orphans a link.
CREATE TABLE IF NOT EXISTS incident_reports (
    report_key  TEXT PRIMARY KEY,
    incident_id INTEGER NOT NULL,
    linked_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_link_incident
    ON incident_reports(incident_id);

-- Every consequential decision, append-only. For an authority to be
-- answerable, something has to record what they did and when. This is also
-- what makes review after the event possible, which is how emergency services
-- actually improve.
CREATE TABLE IF NOT EXISTS verifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    decided_by  TEXT NOT NULL,
    decision    TEXT NOT NULL,   -- confirmed|rejected|duplicate|ground_check
    note        TEXT NOT NULL DEFAULT '',
    decided_at  TEXT NOT NULL
);
"""


@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(SCHEMA)


def already_seen(conn: sqlite3.Connection, wa_message_id: str) -> bool:
    """True if we have handled this message before.

    Meta retries when we are slow. The restaurant bot remembered the last 500
    ids in a Python set, which died with the process. Here the UNIQUE constraint
    does it, so a restart changes nothing.
    """
    row = conn.execute(
        "SELECT 1 FROM raw_messages WHERE wa_message_id = ?", (wa_message_id,)
    ).fetchone()
    return row is not None
