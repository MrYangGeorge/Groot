"""Pure logic for turning raw readings into derived state.

No DB calls, no Viam calls — just functions from inputs to outputs. This is
what makes the poller and the mock generator share the same code path.
"""
from __future__ import annotations

from typing import Any

from buckets import DIMENSIONS, WATERING_DELTA_THRESHOLD


def process_readings(
    new_readings: list[dict],
    last_known_states: dict[str, str | None],
    last_moisture_value: float | None,
) -> dict[str, Any]:
    """Compute state transitions and watering events from a batch of readings.

    Readings MUST be in chronological order. Each reading is the normalized
    dict produced by `viam_client.fetch_readings_since` (or `mock_data.py`):
        {"timestamp": iso_str, "soil_moisture": ..., "temperature_c": ..., ...}
    """
    transitions: list[dict] = []
    waterings: list[dict] = []

    current_states: dict[str, str | None] = dict(last_known_states)
    prev_moisture: float | None = last_moisture_value
    final_timestamp: str | None = None

    for reading in new_readings:
        timestamp = reading.get("timestamp")
        if not timestamp:
            continue
        final_timestamp = timestamp

        for dim_name, (field, bucket_fn) in DIMENSIONS.items():
            value = reading.get(field)
            if value is None:
                continue
            new_state = bucket_fn(float(value))
            old_state = current_states.get(dim_name)
            if new_state != old_state:
                transitions.append(
                    {
                        "timestamp": timestamp,
                        "dimension": dim_name,
                        "from_state": old_state,
                        "to_state": new_state,
                        "trigger_value": float(value),
                    }
                )
                current_states[dim_name] = new_state

        moisture = reading.get("soil_moisture")
        if moisture is not None:
            moisture = float(moisture)
            if (
                prev_moisture is not None
                and (moisture - prev_moisture) >= WATERING_DELTA_THRESHOLD
            ):
                waterings.append(
                    {
                        "timestamp": timestamp,
                        "moisture_before": prev_moisture,
                        "moisture_after": moisture,
                    }
                )
            prev_moisture = moisture

    return {
        "transitions": transitions,
        "waterings": waterings,
        "final_states": current_states,
        "final_moisture_value": prev_moisture,
        "final_timestamp": final_timestamp,
    }
