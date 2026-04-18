"""Public API for producing plant utterances.

Two entry points:
  - answer_user_query(question)         conversational, two Claude calls
  - autonomous_message(event)           unprompted, one Claude call

Both return a dict with the inserted `plant_messages` row fields
(mood, message, urgency, id, timestamp, ...). Both are bulletproof:
on any LLM or DB failure they log and return a graceful fallback
message so the demo never silently dies.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import db
from context_builder import (
    format_recent_messages_block,
    get_current_state_summary,
    get_recent_history_summary,
)
from llm_client import call_claude
from persona import SYSTEM_PROMPT
from text_to_sql import question_to_sql_result

log = logging.getLogger("responder")

VALID_MOODS = {
    "happy", "content", "anxious", "grumpy",
    "dramatic", "desperate", "sleepy", "smug",
}
VALID_URGENCIES = {"low", "medium", "high"}

FALLBACK_MESSAGE = {
    "mood": "sleepy",
    "message": "Mmm, give me a moment — my thoughts are a little drowsy just now.",
    "urgency": "low",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_sql_rows(rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    shown = rows[:10]
    out_lines = [json.dumps(r, default=str) for r in shown]
    if len(rows) > len(shown):
        out_lines.append(f"... ({len(rows) - len(shown)} more rows)")
    return "\n".join(out_lines)


def _validate_message_dict(obj: Any) -> dict:
    """Coerce an LLM response into a validated {mood, message, urgency} dict."""
    if not isinstance(obj, dict):
        raise ValueError("LLM response was not a JSON object")
    mood = str(obj.get("mood", "")).strip().lower()
    urgency = str(obj.get("urgency", "")).strip().lower()
    message = obj.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("missing or empty 'message' field")
    if mood not in VALID_MOODS:
        log.warning("LLM returned unknown mood=%r; defaulting to 'content'", mood)
        mood = "content"
    if urgency not in VALID_URGENCIES:
        log.warning("LLM returned unknown urgency=%r; defaulting to 'low'", urgency)
        urgency = "low"
    return {
        "mood": mood,
        "message": message.strip(),
        "urgency": urgency,
    }


def _log_and_insert(
    trigger_type: str,
    parsed: dict,
    user_query: str | None,
) -> dict:
    timestamp = _now_iso()
    try:
        new_id = db.insert_plant_message(
            timestamp=timestamp,
            trigger_type=trigger_type,
            message=parsed["message"],
            mood=parsed["mood"],
            urgency=parsed["urgency"],
            user_query=user_query,
        )
    except Exception:
        log.exception("Failed to insert plant_message; returning anyway.")
        new_id = 0

    return {
        "id": new_id,
        "timestamp": timestamp,
        "trigger_type": trigger_type,
        "user_query": user_query,
        **parsed,
    }


def _fallback_insert(trigger_type: str, user_query: str | None) -> dict:
    log.warning("Using fallback plant message for trigger_type=%s", trigger_type)
    return _log_and_insert(trigger_type, dict(FALLBACK_MESSAGE), user_query)


def _describe_event(event: dict) -> str:
    etype = event.get("type")
    if etype == "transition":
        return (
            f"A {event.get('dimension')} transition just occurred: "
            f"{event.get('from_state') or 'unknown'} -> {event.get('to_state')} "
            f"at {event.get('timestamp')} (value {event.get('trigger_value')})."
        )
    if etype == "watering":
        return (
            "A watering event just occurred: moisture jumped from "
            f"{event.get('moisture_before')} to {event.get('moisture_after')} "
            f"at {event.get('timestamp')}."
        )
    return f"An event occurred: {json.dumps(event, default=str)}"


def answer_user_query(question: str) -> dict:
    """Conversational path. Two Claude calls: text-to-SQL, then persona."""
    question = (question or "").strip()
    if not question:
        return _fallback_insert("user_query", question)

    log.info("answer_user_query: %r", question)

    try:
        sql_result = question_to_sql_result(question)
    except Exception:
        log.exception("text_to_sql crashed; continuing without SQL context.")
        sql_result = {"sql": None, "rows": [], "answerable": False, "error": "crashed"}

    if sql_result["answerable"]:
        sql_block = (
            f"I ran this query to help answer:\n  {sql_result['sql']}\n"
            f"Results:\n{_format_sql_rows(sql_result['rows'])}"
        )
        answerable_note = "You have relevant sensor history available below."
    elif sql_result["error"]:
        sql_block = (
            "I tried to query my sensor history but something went wrong: "
            f"{sql_result['error']}. Answer from your current state only."
        )
        answerable_note = "No sensor query succeeded."
    else:
        sql_block = (
            "The user asked something that is not about your care, environment, "
            "or sensor history. Respond in character that this is outside what "
            "you can perceive — you are a plant, after all."
        )
        answerable_note = "Question is outside your sensory domain."

    try:
        current_state = get_current_state_summary()
    except Exception:
        log.exception("current_state_summary failed")
        current_state = "(current state unavailable)"
    try:
        history = get_recent_history_summary(hours=24)
    except Exception:
        log.exception("recent_history_summary failed")
        history = "(recent history unavailable)"
    try:
        recent_msgs = format_recent_messages_block(limit=3)
    except Exception:
        log.exception("recent messages lookup failed")
        recent_msgs = "(no previous messages)"

    user_prompt = f"""\
A human has just spoken to you. Respond in character, in JSON, following
all voice rules from the system prompt.

USER QUESTION:
{question}

YOUR CURRENT STATE:
{current_state}

LAST 24 HOURS:
{history}

RECENT THINGS YOU HAVE SAID (do not repeat them):
{recent_msgs}

NOTES ON THIS QUESTION:
{answerable_note}

{sql_block}

Respond now with your JSON object."""

    try:
        parsed = call_claude(
            SYSTEM_PROMPT,
            user_prompt,
            max_tokens=400,
            expect_json=True,
        )
        validated = _validate_message_dict(parsed)
    except Exception:
        log.exception("Claude persona call failed; using fallback.")
        return _fallback_insert("user_query", question)

    return _log_and_insert("user_query", validated, question)


def autonomous_message(transition_or_event: dict) -> dict:
    """Triggered path: poller notices something interesting, plant reacts."""
    log.info("autonomous_message: %s", transition_or_event.get("type"))

    try:
        current_state = get_current_state_summary()
    except Exception:
        log.exception("current_state_summary failed")
        current_state = "(current state unavailable)"
    try:
        recent_msgs = format_recent_messages_block(limit=3)
    except Exception:
        log.exception("recent messages lookup failed")
        recent_msgs = "(no previous messages)"

    event_description = _describe_event(transition_or_event)

    user_prompt = f"""\
Something has just changed in your environment and you feel compelled to
say something, unprompted, to your human. Respond in character, in JSON,
following all voice rules from the system prompt.

TRIGGERING EVENT:
{event_description}

YOUR CURRENT STATE:
{current_state}

RECENT THINGS YOU HAVE SAID (do not repeat them):
{recent_msgs}

Respond now with your JSON object."""

    try:
        parsed = call_claude(
            SYSTEM_PROMPT,
            user_prompt,
            max_tokens=400,
            expect_json=True,
        )
        validated = _validate_message_dict(parsed)
    except Exception:
        log.exception("Claude autonomous call failed; using fallback.")
        return _fallback_insert("autonomous", None)

    return _log_and_insert("autonomous", validated, None)
