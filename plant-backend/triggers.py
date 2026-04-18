"""Decides WHEN the plant should speak unprompted.

Pure policy. Takes in the freshly-processed transitions/waterings from the
poller plus the timestamp of the plant's last utterance, and returns either
the event dict to hand to `responder.autonomous_message` or None.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone

log = logging.getLogger("triggers")

COOLDOWN = timedelta(minutes=5)

URGENT_STATES = {
    "critical",
    "thirsty",
    "drowning",
    "freezing",
    "hot",
    "empty",
}

NOTABLE_EVERY_OTHER_CHANCE = 0.5


def _in_cooldown(last_message_at: datetime | None, now: datetime) -> bool:
    if last_message_at is None:
        return False
    if last_message_at.tzinfo is None:
        last_message_at = last_message_at.replace(tzinfo=timezone.utc)
    return (now - last_message_at) < COOLDOWN


def _transition_event(t: dict) -> dict:
    return {
        "type": "transition",
        "dimension": t.get("dimension"),
        "from_state": t.get("from_state"),
        "to_state": t.get("to_state"),
        "trigger_value": t.get("trigger_value"),
        "timestamp": t.get("timestamp"),
    }


def _watering_event(w: dict) -> dict:
    return {
        "type": "watering",
        "moisture_before": w.get("moisture_before"),
        "moisture_after": w.get("moisture_after"),
        "timestamp": w.get("timestamp"),
    }


def should_speak(
    new_transitions: list[dict],
    new_waterings: list[dict],
    last_message_at: datetime | None,
) -> dict | None:
    now = datetime.now(timezone.utc)
    if _in_cooldown(last_message_at, now):
        log.debug("triggers: in cooldown, staying quiet.")
        return None

    # Rule 2: urgent transition trumps everything else.
    for t in new_transitions:
        if t.get("to_state") in URGENT_STATES:
            log.info(
                "triggers: urgent transition %s -> %s",
                t.get("from_state"),
                t.get("to_state"),
            )
            return _transition_event(t)

    # Rule 3: new watering = demo punchline.
    if new_waterings:
        latest = new_waterings[-1]
        log.info("triggers: watering event (%s -> %s)",
                 latest.get("moisture_before"), latest.get("moisture_after"))
        return _watering_event(latest)

    # Rule 4: notable non-comfortable transitions, 50% chance.
    notable = [
        t for t in new_transitions
        if t.get("to_state") and t.get("to_state") != "comfortable"
    ]
    if notable and random.random() < NOTABLE_EVERY_OTHER_CHANCE:
        t = notable[-1]
        log.info("triggers: notable transition %s -> %s (coin flip won)",
                 t.get("from_state"), t.get("to_state"))
        return _transition_event(t)

    return None
