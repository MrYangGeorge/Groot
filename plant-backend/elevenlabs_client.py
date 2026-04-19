"""Thin wrapper around ElevenLabs STT (Scribe) + TTS (Flash v2.5).

Exposes three functions:
  - get_client()                       lazy SDK client
  - transcribe(audio_bytes, ...) -> str
  - synthesize(text, output_path) -> str

Raises RuntimeError on API failure; the voice loop catches and degrades.
"""
from __future__ import annotations

import logging
import time
from io import BytesIO
from typing import Any

from elevenlabs import ElevenLabs

from config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_STT_MODEL,
    ELEVENLABS_TTS_MODEL,
    ELEVENLABS_VOICE_ID,
    require_elevenlabs_credentials,
)

log = logging.getLogger("elevenlabs_client")

_client: ElevenLabs | None = None


def get_client() -> ElevenLabs:
    global _client
    if _client is None:
        require_elevenlabs_credentials()
        _client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    return _client


def transcribe(audio_bytes: bytes, sample_rate: int = 16000) -> str:
    """Send WAV-encoded audio to ElevenLabs Scribe. Returns transcript text."""
    if not audio_bytes:
        raise RuntimeError("transcribe called with empty audio")

    client = get_client()
    duration_s = max(0.0, len(audio_bytes) / (sample_rate * 2))  # 16-bit mono
    start = time.perf_counter()
    try:
        buf = BytesIO(audio_bytes)
        buf.name = "audio.wav"  # some SDK versions inspect .name for mimetype
        resp: Any = client.speech_to_text.convert(
            file=buf,
            model_id=ELEVENLABS_STT_MODEL,
        )
    except Exception as exc:
        log.exception("ElevenLabs STT failed")
        raise RuntimeError(f"STT failed: {exc}") from exc

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    text = getattr(resp, "text", None)
    if text is None and isinstance(resp, dict):
        text = resp.get("text")
    if not isinstance(text, str):
        raise RuntimeError(f"STT returned unexpected shape: {type(resp).__name__}")

    log.info(
        "stt ok model=%s audio_s=%.2f elapsed_ms=%.0f chars=%d",
        ELEVENLABS_STT_MODEL,
        duration_s,
        elapsed_ms,
        len(text),
    )
    return text.strip()


def synthesize(text: str, output_path: str) -> str:
    """Call ElevenLabs TTS (Flash v2.5). Writes MP3 bytes to `output_path`."""
    text = (text or "").strip()
    if not text:
        raise RuntimeError("synthesize called with empty text")

    client = get_client()
    start = time.perf_counter()
    try:
        chunks = client.text_to_speech.convert(
            voice_id=ELEVENLABS_VOICE_ID,
            model_id=ELEVENLABS_TTS_MODEL,
            text=text,
        )
        audio = b"".join(chunks)
    except Exception as exc:
        log.exception("ElevenLabs TTS failed")
        raise RuntimeError(f"TTS failed: {exc}") from exc

    if not audio:
        raise RuntimeError("TTS returned empty audio")

    try:
        with open(output_path, "wb") as fh:
            fh.write(audio)
    except OSError as exc:
        raise RuntimeError(f"failed to write TTS audio to {output_path}: {exc}") from exc

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    log.info(
        "tts ok model=%s voice=%s chars=%d bytes=%d elapsed_ms=%.0f path=%s",
        ELEVENLABS_TTS_MODEL,
        ELEVENLABS_VOICE_ID,
        len(text),
        len(audio),
        elapsed_ms,
        output_path,
    )
    return output_path
