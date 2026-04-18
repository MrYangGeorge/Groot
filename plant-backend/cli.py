"""Manual testing CLI for the LLM layer.

Usage:
  python cli.py "how are you feeling?"
  python cli.py "when did I last water you?"
  python cli.py --autonomous '{"type":"watering","moisture_before":24.1,"moisture_after":78.3,"timestamp":"2026-04-18T15:22:00Z"}'
  python cli.py --recent 10
  python cli.py --state
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

import db
from context_builder import get_current_state_summary, get_recent_history_summary


MOOD_BADGES = {
    "happy": "[HAPPY]",
    "content": "[CONTENT]",
    "anxious": "[ANXIOUS]",
    "grumpy": "[GRUMPY]",
    "dramatic": "[DRAMATIC]",
    "desperate": "[DESPERATE]",
    "sleepy": "[SLEEPY]",
    "smug": "[SMUG]",
}

URGENCY_BADGES = {
    "low":    "(urgency: low)",
    "medium": "(urgency: MEDIUM)",
    "high":   "(urgency: HIGH!)",
}


def _print_message(msg: dict) -> None:
    mood = (msg.get("mood") or "").lower()
    urgency = (msg.get("urgency") or "").lower()
    mood_badge = MOOD_BADGES.get(mood, f"[{mood.upper() or '?'}]")
    urg_badge = URGENCY_BADGES.get(urgency, f"(urgency: {urgency or '?'})")
    trigger = msg.get("trigger_type", "?")
    ts = msg.get("timestamp", "")
    print()
    print(f"  {mood_badge} {urg_badge}  <{trigger} @ {ts}>")
    print(f"  \"{msg.get('message', '')}\"")
    if msg.get("user_query"):
        print(f"  (in reply to: {msg['user_query']})")
    print()


def _cmd_recent(limit: int) -> None:
    rows = db.get_recent_plant_messages(limit=limit)
    if not rows:
        print("No plant messages yet.")
        return
    for row in reversed(rows):
        _print_message(row)


def _cmd_state() -> None:
    print("Current state")
    print("-------------")
    print(get_current_state_summary())
    print()
    print("Last 24 hours")
    print("-------------")
    print(get_recent_history_summary(hours=24))


def _cmd_autonomous(event_json: str) -> None:
    try:
        event = json.loads(event_json)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON: {exc}", file=sys.stderr)
        sys.exit(2)
    from responder import autonomous_message
    msg = autonomous_message(event)
    _print_message(msg)


def _cmd_ask(question: str) -> None:
    from responder import answer_user_query
    msg = answer_user_query(question)
    _print_message(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Talking Plant CLI.")
    parser.add_argument("question", nargs="?", help="A question to ask the plant.")
    parser.add_argument(
        "--autonomous",
        metavar="JSON",
        help="Fire an autonomous message with the given event JSON.",
    )
    parser.add_argument(
        "--recent",
        type=int,
        metavar="N",
        help="Show the last N plant messages from the DB.",
    )
    parser.add_argument(
        "--state",
        action="store_true",
        help="Print current state and recent history summary.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show INFO-level logs (LLM calls, SQL, etc.)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_db()

    if args.recent is not None:
        _cmd_recent(args.recent)
        return
    if args.state:
        _cmd_state()
        return
    if args.autonomous:
        _cmd_autonomous(args.autonomous)
        return
    if args.question:
        _cmd_ask(args.question)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
