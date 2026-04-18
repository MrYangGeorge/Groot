"""Periodic poller: Viam Cloud -> processor -> SQLite.

Run directly: `python poller.py`. Schedules a single coroutine every
POLL_INTERVAL_SECONDS and runs until Ctrl-C.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

import db
import viam_client
from buckets import DIMENSIONS
from config import POLL_INTERVAL_SECONDS
from processor import process_readings
from triggers import should_speak

log = logging.getLogger("poller")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _maybe_speak(new_transitions: list[dict], new_waterings: list[dict]) -> None:
    """Decide + fire an autonomous message. Never allowed to crash the poller."""
    try:
        last_ts = _parse_iso(db.get_last_plant_message_timestamp())
        event = should_speak(new_transitions, new_waterings, last_ts)
        if not event:
            return
        # Import lazily so the poller can run without ANTHROPIC_API_KEY set
        # when there's nothing to say.
        from responder import autonomous_message
        msg = autonomous_message(event)
        log.info("autonomous message fired (mood=%s, urgency=%s): %s",
                 msg.get("mood"), msg.get("urgency"), msg.get("message"))
    except Exception:
        log.exception("autonomous message flow failed; skipping.")


async def poll_once() -> None:
    try:
        state = db.get_poller_state()
        current_states = {
            dim: db.get_last_state_for_dimension(dim) for dim in DIMENSIONS
        }

        new_readings = await viam_client.fetch_readings_since(
            state["last_seen_timestamp"]
        )
        if not new_readings:
            log.info("No new readings from Viam.")
            return

        result = process_readings(
            new_readings,
            current_states,
            state["last_moisture_value"],
        )

        db.insert_transitions_bulk(result["transitions"])
        db.insert_waterings_bulk(result["waterings"])
        db.update_poller_state(
            result["final_timestamp"] or state["last_seen_timestamp"],
            result["final_moisture_value"],
        )

        log.info(
            "Processed %d readings, %d transitions, %d waterings",
            len(new_readings),
            len(result["transitions"]),
            len(result["waterings"]),
        )

        _maybe_speak(result["transitions"], result["waterings"])
    except Exception:
        log.exception("Poll tick failed; scheduler will continue.")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_db()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll_once,
        trigger=IntervalTrigger(seconds=POLL_INTERVAL_SECONDS),
        next_run_time=None,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("Poller started (interval=%ds). Running initial poll...", POLL_INTERVAL_SECONDS)

    await poll_once()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Signal handlers aren't supported on Windows; Ctrl-C will still
            # bubble up as KeyboardInterrupt.
            pass

    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=False)
        log.info("Poller stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
