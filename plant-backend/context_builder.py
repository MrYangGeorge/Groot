"""Assemble prompt-ready context payloads from the SQLite DB.

All functions here are pure reads — no writes, no LLM calls. They return
either strings (formatted for dropping into a prompt) or small lists/dicts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import db


DIMENSION_LABELS = {
    "moisture": "Moisture",
    "temperature": "Temperature",
    "humidity": "Humidity",
    "light": "Light",
    "water_level": "Water level",
}


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        normalized = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _humanize_delta(then: datetime, now: datetime) -> str:
    delta = now - then
    if delta.total_seconds() < 0:
        return "just now"
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def get_current_state_summary() -> str:
    """Multi-line human-readable summary of the most recent state per dimension."""
    latest = db.get_latest_transition_per_dimension()
    now = datetime.now(timezone.utc)

    lines: list[str] = []
    for key, label in DIMENSION_LABELS.items():
        row = latest.get(key)
        if row is None:
            lines.append(f"{label}: unknown (no data yet)")
            continue
        state = row["to_state"]
        value = row["trigger_value"]
        ts = _parse_iso(row["timestamp"])
        when = _humanize_delta(ts, now) if ts else "unknown"
        lines.append(
            f"{label}: {state} (last changed {when}, value {value:.1f})"
        )
    return "\n".join(lines) if lines else "No sensor data available yet."


def get_recent_history_summary(hours: int = 24) -> str:
    """Narrative of the last N hours: major transitions + watering count."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    since_iso = since.isoformat()

    transitions = db.get_transitions_since(since_iso)
    waterings = db.get_waterings_since(since_iso)

    lines: list[str] = []

    watering_ts_set = {w["timestamp"] for w in waterings}

    notable = []
    for t in transitions:
        if t["dimension"] == "moisture" and t["timestamp"] in watering_ts_set:
            continue
        if t["to_state"] in {
            "critical", "thirsty", "drowning",
            "freezing", "hot",
            "dark", "intense",
            "dry", "humid",
            "empty", "low",
            "well_watered",
        }:
            notable.append(t)

    for t in notable[-10:]:
        ts = _parse_iso(t["timestamp"])
        when = ts.strftime("%H:%M") if ts else "unknown"
        frm = t["from_state"] or "unknown"
        lines.append(
            f"- {t['dimension'].capitalize()} went {frm} -> {t['to_state']} at {when}"
        )

    for w in waterings[-5:]:
        ts = _parse_iso(w["timestamp"])
        when = ts.strftime("%H:%M") if ts else "unknown"
        lines.append(
            f"- You were watered at {when} (moisture {w['moisture_before']:.0f} -> {w['moisture_after']:.0f})"
        )

    lines.append(f"- You've been watered {len(waterings)} time(s) in the last {hours}h.")
    return "\n".join(lines) if lines else f"No notable events in the last {hours}h."


def get_recent_messages(limit: int = 3) -> list[str]:
    """Last N things the plant said, newest first."""
    rows = db.get_recent_plant_messages(limit=limit)
    return [r["message"] for r in rows if r.get("message")]


def format_recent_messages_block(limit: int = 3) -> str:
    msgs = get_recent_messages(limit=limit)
    if not msgs:
        return "(no previous messages — this is the first thing you've said)"
    return "\n".join(f"- {m}" for m in msgs)


def get_db_schema_for_llm() -> str:
    """Schema description for the text-to-SQL system prompt."""
    return """\
You are querying a local SQLite database that stores derived state about a
houseplant. The raw sensor readings are NOT in this database — they live in
Viam Cloud. This DB only contains:

TABLE state_transitions
  id             INTEGER PRIMARY KEY
  timestamp      TEXT     -- ISO 8601 UTC string, e.g. '2026-04-18T15:22:00+00:00'
  dimension      TEXT     -- one of: 'moisture', 'temperature', 'humidity', 'light', 'water_level'
  from_state     TEXT     -- nullable (first-ever transition for that dimension)
  to_state       TEXT     -- bucket name; see allowed values below
  trigger_value  REAL     -- the raw sensor value that caused the transition
  created_at     TEXT

TABLE watering_events
  id               INTEGER PRIMARY KEY
  timestamp        TEXT  -- ISO 8601 UTC
  moisture_before  REAL
  moisture_after   REAL
  created_at       TEXT

TABLE plant_messages
  id             INTEGER PRIMARY KEY
  timestamp      TEXT
  trigger_type   TEXT  -- 'autonomous' | 'user_query'
  user_query     TEXT  -- nullable
  mood           TEXT  -- 'happy' | 'content' | 'anxious' | 'grumpy' | 'dramatic' | 'desperate' | 'sleepy' | 'smug'
  message        TEXT
  urgency        TEXT  -- 'low' | 'medium' | 'high'
  created_at     TEXT

Allowed bucket values by dimension (the `to_state` / `from_state` columns):
  moisture:    'critical', 'thirsty', 'getting_thirsty', 'comfortable', 'well_watered', 'drowning'
  temperature: 'freezing', 'cold', 'comfortable', 'warm', 'hot'
  humidity:    'dry', 'comfortable', 'humid'
  light:       'dark', 'dim', 'bright', 'intense'
  water_level: 'empty', 'low', 'ok'

Timestamp rules:
  - timestamps are ISO 8601 strings, NOT native SQLite datetimes
  - compare them lexically (they sort correctly) OR use
    datetime('now', '-1 day'), datetime('now', '-7 days'), etc.
  - "last day" = datetime('now', '-1 day')
  - "last week" = datetime('now', '-7 days')

Guidance:
  - For "when was I last watered?" use watering_events (cleanest source).
  - For "how long have I been thirsty?" reason over state_transitions
    with dimension='moisture'.
  - For "what's my current X?" take the most recent row per dimension
    from state_transitions.
  - For "what did you say earlier?" query plant_messages.

Example rows (state_transitions):
  (12, '2026-04-18T14:02:00+00:00', 'moisture', 'thirsty', 'well_watered', 74.1, ...)
  (13, '2026-04-18T14:30:00+00:00', 'light', 'bright', 'dim', 412.0, ...)

Example rows (watering_events):
  (3, '2026-04-18T14:02:00+00:00', 18.4, 74.1, ...)
"""
