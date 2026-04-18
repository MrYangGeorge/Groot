"""Viam Cloud read client.

Exposes a single async function `fetch_readings_since` that pulls raw sensor
rows from Viam's tabular data service and returns them as normalized dicts.

NOTE: Viam APP-10891 workaround — every SQL query MUST include a literal
`time_received >= CAST('2000-01-01T00:00:00.000Z' AS TIMESTAMP)` clause,
even when we already have a real lower bound. Both clauses are ANDed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from viam.app.viam_client import ViamClient
from viam.rpc.dial import DialOptions

from config import (
    VIAM_API_KEY,
    VIAM_API_KEY_ID,
    VIAM_COMPONENT_NAME,
    VIAM_ORG_ID,
    VIAM_SENTINEL_TIMESTAMP,
    require_viam_credentials,
)

log = logging.getLogger(__name__)

FETCH_LIMIT = 500
READING_FIELDS = (
    "soil_moisture",
    "temperature_c",
    "humidity_percent",
    "water_level_percent",
    "light_level",
)


async def _connect() -> ViamClient:
    require_viam_credentials()
    dial_options = DialOptions.with_api_key(
        api_key=VIAM_API_KEY, api_key_id=VIAM_API_KEY_ID
    )
    return await ViamClient.create_from_dial_options(dial_options)


def _coerce_timestamp(raw: Any) -> str | None:
    """Normalize Viam's `time_received` to an ISO 8601 UTC string."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc).isoformat()
    if isinstance(raw, str):
        return raw
    return str(raw)


def _extract_reading(row: dict) -> dict | None:
    """Pull sensor fields out of a Viam tabular-data row.

    The Pi-side sensor component typically nests values under
    `data.readings.<field>`, but older/newer revisions may flatten them to
    `data.<field>`. Try both and skip the row if neither shape works.
    """
    timestamp = _coerce_timestamp(row.get("time_received"))
    if not timestamp:
        log.warning("Viam row missing time_received; skipping")
        return None

    data = row.get("data") or {}
    if not isinstance(data, dict):
        log.warning("Viam row has non-dict data; skipping")
        return None

    readings = data.get("readings") if isinstance(data.get("readings"), dict) else None

    def _field(name: str) -> Any:
        if readings is not None and name in readings:
            return readings[name]
        return data.get(name)

    reading: dict[str, Any] = {"timestamp": timestamp}
    found_any = False
    for field in READING_FIELDS:
        value = _field(field)
        if value is None:
            continue
        try:
            reading[field] = float(value)
            found_any = True
        except (TypeError, ValueError):
            log.warning("Non-numeric value for %s in Viam row; skipping field", field)

    if not found_any:
        log.warning("Viam row has no known reading fields; skipping")
        return None
    return reading


async def fetch_readings_since(timestamp_iso: str | None) -> list[dict]:
    """Fetch all Viam readings newer than `timestamp_iso`, oldest first.

    Pass `None` on first run to pull from the sentinel start. The returned
    list is already normalized and safe to feed into `processor.process_readings`.
    """
    since = timestamp_iso or VIAM_SENTINEL_TIMESTAMP

    sql = f"""
    SELECT time_received, data
    FROM readings
    WHERE component_name = '{VIAM_COMPONENT_NAME}'
      AND time_received >= CAST('{VIAM_SENTINEL_TIMESTAMP}' AS TIMESTAMP)
      AND time_received > CAST('{since}' AS TIMESTAMP)
    ORDER BY time_received ASC
    LIMIT {FETCH_LIMIT}
    """

    client = await _connect()
    try:
        data_client = client.data_client
        rows = await data_client.tabular_data_by_sql(
            organization_id=VIAM_ORG_ID, sql_query=sql
        )
    finally:
        client.close()

    readings: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        extracted = _extract_reading(row)
        if extracted is not None:
            readings.append(extracted)

    log.info("Fetched %d readings from Viam (since=%s)", len(readings), since)
    return readings
