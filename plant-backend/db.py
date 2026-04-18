"""SQLite persistence layer for derived plant state.

Raw sensor readings live in Viam Cloud — this DB only stores derived data
(bucket transitions, watering events, future LLM messages) plus a one-row
poller checkpoint so we know which Viam timestamps we've already processed.

A fresh connection is opened per function call. That's slower than a shared
connection pool, but it sidesteps thread-safety gotchas and is plenty fast
for a hackathon.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from config import SQLITE_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    dimension TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    trigger_value REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_transitions_timestamp ON state_transitions(timestamp);
CREATE INDEX IF NOT EXISTS idx_transitions_dimension ON state_transitions(dimension);

CREATE TABLE IF NOT EXISTS watering_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    moisture_before REAL NOT NULL,
    moisture_after REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_watering_timestamp ON watering_events(timestamp);

CREATE TABLE IF NOT EXISTS plant_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    user_query TEXT,
    mood TEXT,
    message TEXT NOT NULL,
    urgency TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON plant_messages(timestamp);

CREATE TABLE IF NOT EXISTS poller_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_seen_timestamp TEXT,
    last_moisture_value REAL,
    updated_at TEXT DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO poller_state (id, last_seen_timestamp, last_moisture_value)
VALUES (1, NULL, NULL);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(SQLITE_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


def reset_db() -> None:
    """Drop all tables and recreate them. Used by `mock_data.py --reset`."""
    with _connect() as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS state_transitions;
            DROP TABLE IF EXISTS watering_events;
            DROP TABLE IF EXISTS plant_messages;
            DROP TABLE IF EXISTS poller_state;
            """
        )
        conn.executescript(SCHEMA)


def get_poller_state() -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_seen_timestamp, last_moisture_value FROM poller_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return {"last_seen_timestamp": None, "last_moisture_value": None}
        return {
            "last_seen_timestamp": row["last_seen_timestamp"],
            "last_moisture_value": row["last_moisture_value"],
        }


def update_poller_state(
    last_seen_timestamp: str | None, last_moisture_value: float | None
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE poller_state
               SET last_seen_timestamp = ?,
                   last_moisture_value = ?,
                   updated_at = datetime('now')
             WHERE id = 1
            """,
            (last_seen_timestamp, last_moisture_value),
        )


def get_last_state_for_dimension(dimension: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT to_state
              FROM state_transitions
             WHERE dimension = ?
             ORDER BY id DESC
             LIMIT 1
            """,
            (dimension,),
        ).fetchone()
        return row["to_state"] if row else None


def insert_transition(
    timestamp: str,
    dimension: str,
    from_state: str | None,
    to_state: str,
    trigger_value: float,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO state_transitions
                (timestamp, dimension, from_state, to_state, trigger_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timestamp, dimension, from_state, to_state, trigger_value),
        )


def insert_transitions_bulk(transitions: list[dict]) -> None:
    if not transitions:
        return
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO state_transitions
                (timestamp, dimension, from_state, to_state, trigger_value)
            VALUES (:timestamp, :dimension, :from_state, :to_state, :trigger_value)
            """,
            transitions,
        )


def insert_watering_event(
    timestamp: str, moisture_before: float, moisture_after: float
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO watering_events (timestamp, moisture_before, moisture_after)
            VALUES (?, ?, ?)
            """,
            (timestamp, moisture_before, moisture_after),
        )


def insert_waterings_bulk(waterings: list[dict]) -> None:
    if not waterings:
        return
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO watering_events (timestamp, moisture_before, moisture_after)
            VALUES (:timestamp, :moisture_before, :moisture_after)
            """,
            waterings,
        )


def get_recent_transitions(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, timestamp, dimension, from_state, to_state, trigger_value, created_at
              FROM state_transitions
             ORDER BY id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def insert_plant_message(
    timestamp: str,
    trigger_type: str,
    message: str,
    mood: str | None = None,
    urgency: str | None = None,
    user_query: str | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO plant_messages
                (timestamp, trigger_type, user_query, mood, message, urgency)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (timestamp, trigger_type, user_query, mood, message, urgency),
        )
        return int(cur.lastrowid or 0)


def get_recent_plant_messages(limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, timestamp, trigger_type, user_query, mood, message, urgency, created_at
              FROM plant_messages
             ORDER BY id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_plant_message_timestamp() -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT timestamp FROM plant_messages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["timestamp"] if row else None


def get_latest_transition_per_dimension() -> dict[str, dict]:
    """Return the most recent transition row per dimension, keyed by dimension."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT dimension, to_state, from_state, trigger_value, timestamp
              FROM (
                SELECT dimension, to_state, from_state, trigger_value, timestamp,
                       ROW_NUMBER() OVER (PARTITION BY dimension ORDER BY id DESC) AS rn
                  FROM state_transitions
              )
             WHERE rn = 1
            """
        ).fetchall()
        return {r["dimension"]: dict(r) for r in rows}


def get_transitions_since(since_iso: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, timestamp, dimension, from_state, to_state, trigger_value
              FROM state_transitions
             WHERE timestamp >= ?
             ORDER BY id ASC
            """,
            (since_iso,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_waterings_since(since_iso: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, timestamp, moisture_before, moisture_after
              FROM watering_events
             WHERE timestamp >= ?
             ORDER BY id ASC
            """,
            (since_iso,),
        ).fetchall()
        return [dict(r) for r in rows]


def count_waterings_since(since_iso: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM watering_events WHERE timestamp >= ?",
            (since_iso,),
        ).fetchone()
        return int(row["n"] if row else 0)
