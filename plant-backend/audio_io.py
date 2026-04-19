"""Microphone capture + speaker playback helpers.

Two exported functions:
  - record_while_held(key_is_held, sample_rate=16000) -> bytes
  - play_audio(path) -> None

Recording uses sounddevice + soundfile to produce a WAV-encoded byte blob
shaped for ElevenLabs Scribe (16 kHz, mono, PCM-16). Playback uses whatever
soundfile can decode; MP3 is handled via the libsndfile backend on most
modern installs, and voice.py passes in an MP3 path.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
import soundfile as sf

log = logging.getLogger("audio_io")

CHANNELS = 1
DTYPE = "int16"
MIN_RECORDING_SECONDS = 0.3
POLL_INTERVAL_S = 0.02


def record_while_held(
    key_is_held: Callable[[], bool],
    sample_rate: int = 16000,
) -> bytes:
    """Record from the default mic while `key_is_held()` returns True.

    Returns a WAV-encoded byte string (16 kHz, mono, PCM-16).
    Raises ValueError("recording too short") if under 0.3 s of audio was
    captured, ConnectionError if the mic fails mid-recording.
    """
    chunks: list[np.ndarray] = []
    total_frames = 0

    def _callback(indata, frames, time_info, status):  # noqa: ANN001
        if status:
            log.warning("sounddevice status: %s", status)
        chunks.append(indata.copy())

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=_callback,
        ):
            while key_is_held():
                time.sleep(POLL_INTERVAL_S)
    except sd.PortAudioError as exc:
        log.exception("mic stream failed")
        raise ConnectionError(f"microphone error: {exc}") from exc

    if not chunks:
        raise ValueError("recording too short")

    audio = np.concatenate(chunks, axis=0)
    total_frames = audio.shape[0]
    duration_s = total_frames / float(sample_rate)
    if duration_s < MIN_RECORDING_SECONDS:
        raise ValueError("recording too short")

    buf = BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    data = buf.getvalue()
    log.info("recorded %.2fs (%d bytes wav)", duration_s, len(data))
    return data


def _play_via_ffplay(path: str) -> bool:
    """Try `ffplay` as a fallback when soundfile can't decode MP3."""
    exe = shutil.which("ffplay")
    if not exe:
        return False
    try:
        subprocess.run(
            [exe, "-autoexit", "-nodisp", "-loglevel", "error", path],
            check=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        log.warning("ffplay failed: %s", exc)
        return False


def _play_via_afplay(path: str) -> bool:
    """macOS fallback using the built-in `afplay` command (handles MP3)."""
    if sys.platform != "darwin":
        return False
    exe = shutil.which("afplay")
    if not exe:
        return False
    try:
        subprocess.run([exe, path], check=True)
        return True
    except subprocess.CalledProcessError as exc:
        log.warning("afplay failed: %s", exc)
        return False


def play_audio(path: str) -> None:
    """Play an audio file (WAV or MP3) through the default output device."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)

    # Try soundfile/sounddevice first — works for WAV always, and for MP3 on
    # libsndfile >= 1.1 (shipped by modern soundfile wheels).
    try:
        data, sample_rate = sf.read(str(p), dtype="float32")
    except Exception as sf_exc:
        log.info("soundfile cannot decode %s (%s); trying platform fallback",
                 p.suffix, sf_exc)
        if _play_via_afplay(str(p)):
            return
        if _play_via_ffplay(str(p)):
            return
        raise RuntimeError(
            f"couldn't play {path}: soundfile failed ({sf_exc}) and no "
            "afplay/ffplay fallback is available"
        ) from sf_exc

    try:
        sd.play(data, sample_rate)
        sd.wait()
    except sd.PortAudioError as exc:
        log.exception("playback failed")
        raise RuntimeError(f"playback failed: {exc}") from exc
