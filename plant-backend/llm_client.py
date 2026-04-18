"""Thin wrapper around the Anthropic SDK.

Exposes a single function `call_claude(system, user, ...)` that handles
token logging, latency logging, optional JSON parsing with a single
corrective retry, and markdown-fence stripping.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from anthropic import Anthropic

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    require_anthropic_credentials,
)

log = logging.getLogger("llm_client")

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        require_anthropic_credentials()
        _client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _strip_fences(text: str) -> str:
    """Claude sometimes wraps JSON in ```json ... ``` fences; strip them."""
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _parse_json_or_raise(text: str) -> dict:
    cleaned = _strip_fences(text)
    return json.loads(cleaned)


def _raw_call(system: str, user: str, *, max_tokens: int) -> tuple[str, dict]:
    """Low-level call. Returns (text, usage_meta)."""
    client = _get_client()
    start = time.perf_counter()
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    parts = []
    for block in resp.content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    text = "".join(parts).strip()

    usage = getattr(resp, "usage", None)
    meta = {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "elapsed_ms": round(elapsed_ms, 1),
        "model": CLAUDE_MODEL,
    }
    return text, meta


def call_claude(
    system: str,
    user: str,
    *,
    max_tokens: int = 400,
    expect_json: bool = True,
) -> dict | str:
    """Call Claude. Returns dict if expect_json else str.

    On JSON parse failure with expect_json=True, retries exactly once with a
    corrective message. On API errors, logs and re-raises — the caller
    decides how to degrade.
    """
    try:
        text, meta = _raw_call(system, user, max_tokens=max_tokens)
    except Exception as exc:
        log.exception("Anthropic API call failed: %s", exc)
        raise

    if not expect_json:
        log.info(
            "claude ok model=%s in=%s out=%s elapsed_ms=%s json=False",
            meta["model"],
            meta["input_tokens"],
            meta["output_tokens"],
            meta["elapsed_ms"],
        )
        return text

    try:
        parsed = _parse_json_or_raise(text)
        log.info(
            "claude ok model=%s in=%s out=%s elapsed_ms=%s json=ok",
            meta["model"],
            meta["input_tokens"],
            meta["output_tokens"],
            meta["elapsed_ms"],
        )
        return parsed
    except json.JSONDecodeError as first_err:
        log.warning(
            "claude json parse failed; retrying once. first_response=%r err=%s",
            text[:200],
            first_err,
        )
        corrective_user = (
            user
            + "\n\n---\nYour previous response was not valid JSON:\n"
            + text
            + "\n\nRespond again with ONLY a single valid JSON object "
            "matching the required schema. No markdown, no commentary."
        )
        try:
            text2, meta2 = _raw_call(system, corrective_user, max_tokens=max_tokens)
        except Exception:
            log.exception("Anthropic retry call failed.")
            raise
        try:
            parsed = _parse_json_or_raise(text2)
            log.info(
                "claude ok model=%s in=%s out=%s elapsed_ms=%s json=ok-after-retry",
                meta2["model"],
                meta2["input_tokens"],
                meta2["output_tokens"],
                meta2["elapsed_ms"],
            )
            return parsed
        except json.JSONDecodeError as second_err:
            log.error(
                "claude json parse failed twice. final_response=%r err=%s",
                text2[:200],
                second_err,
            )
            raise ValueError(
                f"Claude returned invalid JSON twice: {second_err}"
            ) from second_err
