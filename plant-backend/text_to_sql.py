"""Natural language -> SQL, with aggressive safety checks.

The plant is allowed to READ its derived state, and nothing else. Anything
that smells like a write, a schema change, or a PRAGMA is rejected before
it ever touches SQLite.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

from config import SQLITE_PATH
from context_builder import get_db_schema_for_llm
from llm_client import call_claude

log = logging.getLogger("text_to_sql")

UNANSWERABLE = "UNANSWERABLE"

FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "CREATE", "ATTACH", "PRAGMA", "REPLACE",
)

_FENCE_RE = re.compile(r"^\s*```(?:sql)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _build_system_prompt() -> str:
    schema = get_db_schema_for_llm()
    return f"""\
You translate a user's natural-language question about a houseplant's
sensor history into a single SQLite SELECT query.

{schema}

Rules:
  1. Output ONLY the SQL query, nothing else. No markdown, no commentary.
  2. SQLite syntax only.
  3. Read-only: SELECT statements ONLY. Never INSERT/UPDATE/DELETE/DROP/ALTER/
     CREATE/ATTACH/PRAGMA/REPLACE.
  4. Always include LIMIT 100 or smaller.
  5. If the question is NOT about the plant's care, environment, sensors,
     or history, output the literal word UNANSWERABLE (no SQL, no
     explanation, just that one word).

Few-shot examples:

Question: When was I last watered?
SQL: SELECT timestamp FROM watering_events ORDER BY timestamp DESC LIMIT 1;

Question: How many times have you been watered this week?
SQL: SELECT COUNT(*) AS times_watered FROM watering_events WHERE timestamp >= datetime('now', '-7 days') LIMIT 100;

Question: What's the longest you've gone thirsty?
SQL: SELECT timestamp, from_state, to_state FROM state_transitions WHERE dimension = 'moisture' ORDER BY timestamp ASC LIMIT 100;

Question: What's my current moisture state?
SQL: SELECT to_state, trigger_value, timestamp FROM state_transitions WHERE dimension = 'moisture' ORDER BY id DESC LIMIT 1;

Question: What did you tell me earlier today?
SQL: SELECT timestamp, message, mood FROM plant_messages WHERE timestamp >= datetime('now', '-1 day') ORDER BY id DESC LIMIT 20;

Question: What's the meaning of life?
SQL: UNANSWERABLE

Question: What's the weather like in Tokyo?
SQL: UNANSWERABLE
"""


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _is_safe_select(sql: str) -> tuple[bool, str | None]:
    """Return (ok, reason_if_bad)."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return False, "empty SQL"

    if ";" in stripped:
        return False, "multiple statements"

    if not re.match(r"^\s*SELECT\b", stripped, re.IGNORECASE):
        return False, "not a SELECT statement"

    upper = stripped.upper()
    for kw in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            return False, f"forbidden keyword: {kw}"

    return True, None


def _execute_readonly(sql: str) -> list[dict]:
    uri = f"file:{SQLITE_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def question_to_sql_result(question: str) -> dict[str, Any]:
    system = _build_system_prompt()
    user = f"Question: {question.strip()}\nSQL:"

    try:
        raw = call_claude(system, user, max_tokens=300, expect_json=False)
    except Exception as exc:
        log.exception("text_to_sql LLM call failed")
        return {
            "sql": None,
            "rows": [],
            "answerable": False,
            "error": f"LLM call failed: {exc}",
        }

    if not isinstance(raw, str):
        return {
            "sql": None,
            "rows": [],
            "answerable": False,
            "error": "unexpected non-string LLM response",
        }

    candidate = _strip_fences(raw)

    if candidate.strip().upper().startswith(UNANSWERABLE):
        log.info("text_to_sql: UNANSWERABLE for question=%r", question)
        return {
            "sql": None,
            "rows": [],
            "answerable": False,
            "error": None,
        }

    ok, reason = _is_safe_select(candidate)
    if not ok:
        log.warning("text_to_sql rejected unsafe SQL (%s): %r", reason, candidate)
        return {
            "sql": candidate,
            "rows": [],
            "answerable": False,
            "error": "unsafe SQL",
        }

    cleaned = candidate.strip().rstrip(";").strip()

    try:
        rows = _execute_readonly(cleaned)
    except sqlite3.Error as exc:
        log.warning("text_to_sql SQL execution failed: %s; sql=%r", exc, cleaned)
        return {
            "sql": cleaned,
            "rows": [],
            "answerable": False,
            "error": f"sqlite error: {exc}",
        }

    log.info("text_to_sql ok: %d rows for question=%r", len(rows), question)
    return {
        "sql": cleaned,
        "rows": rows,
        "answerable": True,
        "error": None,
    }
