"""Spacebar push-to-talk voice loop.

Usage: `python voice.py`
  - Hold [SPACE] to record a question
  - Release to send to STT -> Claude persona -> TTS -> speakers
  - Press [Q] to quit

Strictly a thin audio wrapper around `responder.answer_user_query`.
No autonomous speaking, no database writes of its own.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from pynput import keyboard

import audio_io
import config
import db
import elevenlabs_client
import responder

log = logging.getLogger("voice")

AUDIO_CACHE_DIR = "audio_cache"

space_held = threading.Event()
quit_flag = threading.Event()


URGENCY_BADGES = {"low": "[low]", "medium": "[~]", "high": "[!]"}


def _on_press(key: object) -> bool | None:
    if key == keyboard.Key.space:
        space_held.set()
        return None
    if hasattr(key, "char") and getattr(key, "char", None) == "q":
        quit_flag.set()
        return False
    return None


def _on_release(key: object) -> bool | None:
    if key == keyboard.Key.space:
        space_held.clear()
    return None


def _say(result: dict) -> None:
    """Synthesize + play. Failures are logged, not propagated."""
    message = result.get("message", "")
    msg_id = result.get("id") or int(time.time())
    if not message.strip():
        return
    os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
    output_path = os.path.join(AUDIO_CACHE_DIR, f"response_{msg_id}.mp3")
    try:
        elevenlabs_client.synthesize(message, output_path)
    except Exception as exc:
        print(f"  [warn] tts failed: {exc}")
        return
    try:
        audio_io.play_audio(output_path)
    except Exception as exc:
        print(f"  [warn] playback failed: {exc}")


def _interaction() -> None:
    """One full turn: record -> transcribe -> answer -> synthesize -> play."""
    print(">> listening...")
    try:
        audio_bytes = audio_io.record_while_held(space_held.is_set)
    except ValueError:
        print("  [skip] too short -- hold spacebar longer\n")
        return
    except ConnectionError as exc:
        print(f"  [warn] mic error: {exc}\n")
        return
    except Exception as exc:
        print(f"  [warn] recording failed: {exc}\n")
        return

    print(">> thinking...")
    try:
        transcript = elevenlabs_client.transcribe(audio_bytes)
    except Exception as exc:
        print(f"  [warn] transcription failed: {exc}\n")
        return

    if not transcript.strip():
        print("  [skip] didn't catch anything\n")
        return

    print(f"  you: {transcript}")

    try:
        result = responder.answer_user_query(transcript)
    except Exception as exc:
        log.exception("responder failed")
        print(f"  [warn] responder failed: {exc}\n")
        return

    mood = result.get("mood", "?")
    urgency = (result.get("urgency") or "low").lower()
    badge = URGENCY_BADGES.get(urgency, "[~]")
    print(f"  {badge} [{mood}] {result.get('message', '')}\n")

    _say(result)


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config.require_anthropic_credentials()
        config.require_elevenlabs_credentials()
    except RuntimeError as exc:
        print(f"[error] {exc}")
        return

    db.init_db()

    listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
    listener.start()

    print(f"Hold [SPACE] to talk to {config.PLANT_NAME}. Press [Q] to quit.\n")

    try:
        while not quit_flag.is_set():
            while not space_held.is_set() and not quit_flag.is_set():
                time.sleep(0.05)
            if quit_flag.is_set():
                break
            _interaction()
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
        print("bye")


if __name__ == "__main__":
    main()
