"""Bucketing functions that map raw sensor values to human-readable states."""
from __future__ import annotations

from typing import Callable


def bucket_moisture(v: float) -> str:
    if v >= 80:
        return "drowning"
    if v >= 60:
        return "well_watered"
    if v >= 40:
        return "comfortable"
    if v >= 25:
        return "getting_thirsty"
    if v >= 15:
        return "thirsty"
    return "critical"


def bucket_temperature(v: float) -> str:
    if v < 10:
        return "freezing"
    if v < 15:
        return "cold"
    if v < 28:
        return "comfortable"
    if v < 32:
        return "warm"
    return "hot"


def bucket_humidity(v: float) -> str:
    if v < 30:
        return "dry"
    if v < 70:
        return "comfortable"
    return "humid"


def bucket_light(v: float) -> str:
    if v < 50:
        return "dark"
    if v < 500:
        return "dim"
    if v < 5000:
        return "bright"
    return "intense"


def bucket_water_level(v: float) -> str:
    if v < 10:
        return "empty"
    if v < 30:
        return "low"
    return "ok"


# Maps a logical dimension name to (reading field, bucket function).
DIMENSIONS: dict[str, tuple[str, Callable[[float], str]]] = {
    "moisture": ("soil_moisture", bucket_moisture),
    "temperature": ("temperature_c", bucket_temperature),
    "humidity": ("humidity_percent", bucket_humidity),
    "light": ("light_level", bucket_light),
    "water_level": ("water_level_percent", bucket_water_level),
}

# A jump of this many moisture points between consecutive readings is
# interpreted as someone watering the plant.
WATERING_DELTA_THRESHOLD: float = 15.0
