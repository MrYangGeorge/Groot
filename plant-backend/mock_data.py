"""Standalone mock data generator.

Simulates a day of plant sensor readings without Viam. In `--mode direct`
it runs them through the same `processor.process_readings` the poller uses
and writes derived state to SQLite, so you can demo the pipeline with zero
hardware or Viam access.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import time
from datetime import datetime, timedelta, timezone

import db
from buckets import DIMENSIONS
from processor import process_readings

log = logging.getLogger("mock_data")

SIM_START = datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc)


def _noise(value: float, pct: float = 0.02) -> float:
    return value + random.gauss(0.0, abs(value) * pct)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _generate_day(duration_hours: float, interval_seconds: float) -> list[dict]:
    """Produce a list of synthetic readings covering `duration_hours`."""
    readings: list[dict] = []

    total_seconds = int(duration_hours * 3600)
    step = max(1, int(interval_seconds))

    moisture = 60.0
    last_watering_sim_seconds = 0
    next_watering_gap = random.uniform(5.5, 6.5) * 3600

    for sim_seconds in range(0, total_seconds, step):
        sim_hours = sim_seconds / 3600.0
        ts = SIM_START + timedelta(seconds=sim_seconds)

        # Evaporation: -3%/hour times step fraction.
        moisture -= 3.0 * (step / 3600.0)
        if (sim_seconds - last_watering_sim_seconds) >= next_watering_gap:
            bump = random.uniform(30.0, 50.0)
            moisture = min(100.0, moisture + bump)
            last_watering_sim_seconds = sim_seconds
            next_watering_gap = random.uniform(5.5, 6.5) * 3600
        moisture = _clamp(moisture, 0.0, 100.0)

        # Temperature sine wave peaking at sim noon (hour 12).
        temp_c = 22.0 + 8.0 * math.sin(2 * math.pi * (sim_hours - 6) / 24.0)

        # Humidity loosely inverse to temperature, base 60 ± 15.
        humidity = 60.0 - (temp_c - 22.0) * (15.0 / 8.0)
        humidity = _clamp(humidity, 0.0, 100.0)

        # Light: bell curve centered on sim noon.
        light = 5000.0 * math.exp(-((sim_hours - 12.0) ** 2) / (2 * 3.0**2))
        light = max(0.0, light)

        # Water reservoir: linear 100 -> 60 over duration.
        frac = sim_hours / max(duration_hours, 1e-9)
        water_level = _clamp(100.0 - 40.0 * frac, 0.0, 100.0)

        readings.append(
            {
                "timestamp": ts.isoformat(),
                "soil_moisture": _clamp(_noise(moisture), 0.0, 100.0),
                "temperature_c": _noise(temp_c),
                "humidity_percent": _clamp(_noise(humidity), 0.0, 100.0),
                "water_level_percent": _clamp(_noise(water_level), 0.0, 100.0),
                "light_level": max(0.0, _noise(light) if light > 1 else light),
            }
        )

    return readings


def _run_direct(readings: list[dict], speed: float, interval_seconds: float) -> None:
    """Process readings through the real pipeline, paced by `speed`."""
    state = db.get_poller_state()
    current_states = {dim: db.get_last_state_for_dimension(dim) for dim in DIMENSIONS}

    # Wall-clock delay between readings = sim interval / speed factor.
    wall_delay = max(0.0, interval_seconds / max(speed, 1e-9))

    batch: list[dict] = []
    last_moisture = state["last_moisture_value"]
    last_timestamp = state["last_seen_timestamp"]
    total_transitions = 0
    total_waterings = 0

    # Flush to DB every FLUSH_EVERY readings so the demo feels live.
    FLUSH_EVERY = 20

    for i, reading in enumerate(readings, start=1):
        batch.append(reading)
        if i % FLUSH_EVERY == 0 or i == len(readings):
            result = process_readings(batch, current_states, last_moisture)
            db.insert_transitions_bulk(result["transitions"])
            db.insert_waterings_bulk(result["waterings"])
            last_moisture = result["final_moisture_value"]
            last_timestamp = result["final_timestamp"] or last_timestamp
            current_states = result["final_states"]
            db.update_poller_state(last_timestamp, last_moisture)
            total_transitions += len(result["transitions"])
            total_waterings += len(result["waterings"])
            log.info(
                "Flushed %d readings (cum: %d transitions, %d waterings)",
                len(batch),
                total_transitions,
                total_waterings,
            )
            batch = []

        if wall_delay > 0:
            time.sleep(wall_delay)

    log.info(
        "Done. Generated %d readings -> %d transitions, %d waterings.",
        len(readings),
        total_transitions,
        total_waterings,
    )


def _run_print(readings: list[dict]) -> None:
    for r in readings:
        print(json.dumps(r))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate mock plant sensor data.")
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument(
        "--speed",
        type=float,
        default=600.0,
        help="Wall-clock compression factor. 600 -> 24h sim in ~2.5 min wall clock.",
    )
    parser.add_argument(
        "--mode",
        choices=("direct", "print"),
        default="direct",
        help="direct = write to SQLite via processor, print = emit JSON lines.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all tables before generating.",
    )
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.seed is not None:
        random.seed(args.seed)

    if args.mode == "direct":
        if args.reset:
            log.info("Resetting database at startup.")
            db.reset_db()
        else:
            db.init_db()

    readings = _generate_day(args.duration_hours, args.interval_seconds)
    log.info("Generated %d synthetic readings.", len(readings))

    if args.mode == "print":
        _run_print(readings)
    else:
        _run_direct(readings, args.speed, args.interval_seconds)


if __name__ == "__main__":
    main()
