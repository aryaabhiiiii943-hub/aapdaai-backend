"""Storage. SQLite on your laptop, Postgres on the server, one set of SQL.

WHY BOTH
    Render's free tier has no persistent disk, so a SQLite file is wiped on
    every deploy - every report, every incident, every decision an officer
    made. Fine while nothing mattered; not fine now.

    But requiring a Postgres server to test a regex change would be its own
    kind of stupid. So: DATABASE_URL set means Postgres, absent means a local
    file, and nothing else in the codebase knows the difference.

THE ONE TRICK
    Queries are written in SQLite's style with `?` placeholders and translated
    to `%s` at the single point where they're executed - the same approach as
    the restaurant bot, for the same reason: one place to change instead of
    every call site.

    Placeholders matter beyond convenience. Passing params separately means the
    database treats them as data, never as SQL. Build a query with an f-string
    and a reporter can put SQL in their message.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager

from app.config import DATABASE_PATH, DATABASE_URL

IS_POSTGRES = bool(DATABASE_URL)

if IS_POSTGRES:                                   # pragma: no cover
    import psycopg2
    from psycopg2.extras import RealDictCursor

# The only two places the dialects genuinely differ.
_PK = "SERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
_BOOL = "BOOLEAN" if IS_POSTGRES else "INTEGER"


SCHEMA = f"""
-- Every message exactly as WhatsApp sent it, before we understand any of it.
-- If our parser turns out to be wrong at 2am on the 28th, this is what saves us.
CREATE TABLE IF NOT EXISTS raw_messages (
    id            {_PK},
    wa_message_id TEXT UNIQUE NOT NULL,   -- UNIQUE is the dedup. Let the DB do it.
    from_number   TEXT NOT NULL,
    kind          TEXT NOT NULL,          -- text | location | image | audio | ...
    payload       TEXT NOT NULL,          -- the whole JSON, untouched
    received_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_from ON raw_messages(from_number);

-- One row per phone number we are mid-conversation with: what we last asked
-- them and what they've answered. Survives a restart, unlike the dict the
-- restaurant bot used - which is the bug that lost a customer's order.
CREATE TABLE IF NOT EXISTS conversations (
    phone      TEXT PRIMARY KEY,
    state      TEXT NOT NULL,             -- JSON
    updated_at TEXT NOT NULL
);

-- WHAT IS STORED HERE AND WHAT ISN'T
--
--   Stored     : identity and decisions. An incident's id, where it is, and
--                what a human decided about it. None of that can be
--                recalculated, and losing it loses the human's work.
--
--   Not stored : headcount, severity, confidence, what to send. Those are pure
--                functions of the reports attached, so they're computed on
--                read. A stored copy could disagree with the reports beneath
--                it, and then which one is true?
CREATE TABLE IF NOT EXISTS incidents (
    id           {_PK},
    lat          REAL NOT NULL,
    lng          REAL NOT NULL,
    place_text   TEXT NOT NULL DEFAULT '',
    confirmation TEXT NOT NULL DEFAULT 'unconfirmed',
    response     TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- Which report belongs to which incident. `report_key` is stable across
-- rebuilds (see Need.key), so re-deriving reports never orphans a link.
CREATE TABLE IF NOT EXISTS incident_reports (
    report_key  TEXT PRIMARY KEY,
    incident_id INTEGER NOT NULL,
    linked_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_link_incident ON incident_reports(incident_id);

-- Every consequential decision, append-only. For an authority to be
-- answerable, something has to record what they did and when.
CREATE TABLE IF NOT EXISTS verifications (
    id          {_PK},
    incident_id INTEGER NOT NULL,
    decided_by  TEXT NOT NULL,
    decision    TEXT NOT NULL,   -- confirmed|rejected|duplicate|ground_check
    note        TEXT NOT NULL DEFAULT '',
    decided_at  TEXT NOT NULL
);

-- THINGS THAT MOVE.
-- Seeded, and honestly so: no Indian state publishes live ambulance positions.
-- What matters is that the shape is right, so a real feed drops in later
-- without changing anything above it.
CREATE TABLE IF NOT EXISTS resources (
    id         {_PK},
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,   -- ambulance|rescue_team|fire_truck|boat|heavy_rescue
    lat        REAL NOT NULL,
    lng        REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'available',  -- available|deployed|offline
    org        TEXT NOT NULL DEFAULT '',
    capacity   INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_res_kind ON resources(kind, status);

-- THINGS THAT DON'T MOVE, and fill up instead.
-- A full shelter behaves nothing like a busy ambulance, which is why this
-- isn't the same table.
CREATE TABLE IF NOT EXISTS facilities (
    id        {_PK},
    name      TEXT NOT NULL,
    kind      TEXT NOT NULL,   -- hospital|shelter
    lat       REAL NOT NULL,
    lng       REAL NOT NULL,
    capacity  INTEGER NOT NULL DEFAULT 0,
    occupancy INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS road_blocks (
    id          {_PK},
    lat         REAL NOT NULL,
    lng         REAL NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    active      {_BOOL} NOT NULL DEFAULT TRUE,
    reported_at TEXT NOT NULL
);

-- What makes "deployed" mean something. Without this row a committed ambulance
-- keeps being recommended to the next incident, and two critical incidents
-- quietly get sent the same vehicle.
CREATE TABLE IF NOT EXISTS assignments (
    id          {_PK},
    incident_id INTEGER NOT NULL,
    resource_id INTEGER NOT NULL,
    purpose     TEXT NOT NULL DEFAULT 'response',  -- response|ground_check
    status      TEXT NOT NULL DEFAULT 'assigned',  -- assigned|arrived|released
    assigned_at TEXT NOT NULL,
    released_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_assign_incident ON assignments(incident_id);

-- DID HELP ACTUALLY REACH THEM?
--
-- The question nobody can answer today. Calls get logged, units get tasked,
-- and whether anyone actually arrived lives in a duty officer's memory.
--
-- 'assigned' means WE sent something. This table is what the PERSON THERE
-- experienced, which is the only version that counts. A map that turns green
-- because a truck was dispatched is a map that lies.
CREATE TABLE IF NOT EXISTS arrival_checks (
    id          {_PK},
    incident_id INTEGER NOT NULL,
    reporter    TEXT NOT NULL,
    asked_at    TEXT NOT NULL,
    replied_at  TEXT,
    arrived     {_BOOL},          -- NULL until they answer
    note        TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_arrival_incident
    ON arrival_checks(incident_id);
"""


class Conn:
    """A connection that behaves the same on both engines.

    Exists so the rest of the codebase can keep calling `conn.execute(sql, p)`
    with `?` placeholders, whichever database is underneath.
    """

    def __init__(self, raw, is_postgres: bool):
        self.raw = raw
        self.is_postgres = is_postgres

    def _translate(self, sql: str) -> str:
        if not self.is_postgres:
            return sql
        # `?` outside of string literals becomes `%s`. Our SQL has no literal
        # question marks, so a plain replace is safe and obvious.
        return sql.replace("?", "%s")

    def execute(self, sql: str, params=()):
        cur = self.raw.cursor()
        cur.execute(self._translate(sql), tuple(params))
        return cur

    def executescript(self, script: str) -> None:
        if self.is_postgres:
            # psycopg2 runs multiple statements in one execute; sqlite needs
            # its own method. Comments survive both.
            self.raw.cursor().execute(script)
        else:
            self.raw.executescript(script)

    def insert_id(self, sql: str, params=()) -> int:
        """INSERT and return the new row's id, on either engine."""
        if self.is_postgres:
            cur = self.execute(sql.rstrip().rstrip(";") + " RETURNING id", params)
            row = cur.fetchone()
            return row["id"] if isinstance(row, dict) else row[0]
        return self.execute(sql, params).lastrowid


@contextmanager
def get_db():
    if IS_POSTGRES:                               # pragma: no cover
        raw = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    else:
        raw = sqlite3.connect(DATABASE_PATH)
        raw.row_factory = sqlite3.Row

    conn = Conn(raw, IS_POSTGRES)
    try:
        yield conn
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(SCHEMA)
    print(f"[db] ready ({'postgres' if IS_POSTGRES else DATABASE_PATH})")


def already_seen(conn: Conn, wa_message_id: str) -> bool:
    """True if we have handled this message before.

    Meta retries when we are slow. The restaurant bot remembered the last 500
    ids in a Python set, which died with the process. Here the UNIQUE
    constraint does it, so a restart changes nothing.
    """
    row = conn.execute(
        "SELECT 1 FROM raw_messages WHERE wa_message_id = ?",
        (wa_message_id,)).fetchone()
    return row is not None
