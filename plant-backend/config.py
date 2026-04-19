"""Loads environment configuration from .env and exports it as constants."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)

VIAM_API_KEY: str = os.getenv("VIAM_API_KEY", "")
VIAM_API_KEY_ID: str = os.getenv("VIAM_API_KEY_ID", "")
VIAM_ORG_ID: str = os.getenv("VIAM_ORG_ID", "")
VIAM_COMPONENT_NAME: str = os.getenv("VIAM_COMPONENT_NAME", "plant_sensor")

SQLITE_PATH: str = os.getenv("SQLITE_PATH", "./plant.db")
POLL_INTERVAL_SECONDS: int = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-opus-4-5")
PLANT_NAME: str = os.getenv("PLANT_NAME", "Groot")
PLANT_SPECIES: str = os.getenv("PLANT_SPECIES", "monstera deliciosa")

ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_TTS_MODEL: str = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
ELEVENLABS_STT_MODEL: str = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v1")

# Sentinel lower bound required by Viam APP-10891 workaround — every SQL query
# must include a literal >= this timestamp clause, even when a real lower
# bound also exists.
VIAM_SENTINEL_TIMESTAMP: str = "2000-01-01T00:00:00.000Z"


def require_anthropic_credentials() -> None:
    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY == "replace-me":
        raise RuntimeError(
            "Missing ANTHROPIC_API_KEY in .env. Copy .env.example and fill "
            "it in before running LLM features."
        )


def require_elevenlabs_credentials() -> None:
    missing = [
        name
        for name, value in (
            ("ELEVENLABS_API_KEY", ELEVENLABS_API_KEY),
            ("ELEVENLABS_VOICE_ID", ELEVENLABS_VOICE_ID),
        )
        if not value or value == "replace-me"
    ]
    if missing:
        raise RuntimeError(
            f"Missing ElevenLabs configuration in .env: {', '.join(missing)}. "
            "Pick a voice at https://elevenlabs.io/app/voice-lab and paste "
            "the voice ID into ELEVENLABS_VOICE_ID."
        )


def require_viam_credentials() -> None:
    """Raise a clear error if Viam creds are missing. Call from live paths only."""
    missing = [
        name
        for name, value in (
            ("VIAM_API_KEY", VIAM_API_KEY),
            ("VIAM_API_KEY_ID", VIAM_API_KEY_ID),
            ("VIAM_ORG_ID", VIAM_ORG_ID),
        )
        if not value or value == "replace-me"
    ]
    if missing:
        raise RuntimeError(
            f"Missing Viam credentials in .env: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in real values."
        )
