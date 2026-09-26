# backend/models.py
"""
models.py — Nemotron API wrapper. One entry point, `call_nemotron()`, used by
every agent (bisector.py now; diagnoser.py/fixer.py in Stack 2) for every
model call in this project.

Implements, verbatim per AGENT_SPECS.md §0 / AGENTS.md:
  - strict JSON out; on malformed JSON, retry once with a corrective nudge,
    then raise rather than guess at a result
  - every prompt (full message list) + raw response logged, keyed by job_id
    and step
  - temperature routing: Nano is Bisector-only, 0.1-0.3 (AGENTS.md #4); Ultra
    spans Fixer (0.1-0.3) and Diagnoser (0.1-0.5, explanation phrasing only —
    the classification field must still be deterministic). This module can't
    enforce the "one field deterministic, one field looser" split — that's a
    single generation call, so it's one temperature for the whole response.
    Diagnoser's own prompt design (Stack 2) has to resolve that tension;
    flagging it here rather than silently picking one number for it.

*** Real API surface — NOT verified against a live account, read before you
    wire in NEBIUS_API_KEY ***
This targets Nebius Token Factory's OpenAI-compatible /chat/completions
endpoint. From public docs/search (no live account access in this session):
  - base_url candidates seen: "https://api.tokenfactory.nebius.com/v1" and
    a region-qualified "https://api.tokenfactory.us-central1.nebius.com/v1/"
    — these may not be the same thing. Confirm yours from the Token Factory
    console and set NEBIUS_API_BASE_URL if it differs from the default below.
  - model ID casing was inconsistent across sources for Nano/Ultra. Defaults
    below are my best read, not a confirmed catalog ID — check the console's
    model catalog page and set NEMOTRON_NANO_MODEL_ID / NEMOTRON_ULTRA_MODEL_ID
    if they don't match.
  - bigger issue: Nebius has reported that their hosted Nemotron models are
    "reasoning" models that can return the actual answer in a
    `reasoning_content` field with `content` left EMPTY, rather than the
    reverse. If this wrapper only read `content`, a normal reasoning
    response would look identical to "the model returned no JSON" and burn
    the one malformed-output retry for nothing every single call. This
    wrapper reads whichever field is non-empty (see `_message_text`) — but
    that's inferred from a bug report, not confirmed against your account.
    Make ONE live call before trusting this in the Bisector loop.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Literal

import httpx
import structlog

log = structlog.get_logger("models")

ModelName = Literal["nano", "ultra"]

_MODEL_IDS: dict[ModelName, str] = {
    "nano": os.environ.get("NEMOTRON_NANO_MODEL_ID", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"),
    "ultra": os.environ.get("NEMOTRON_ULTRA_MODEL_ID", "nvidia/Nemotron-3-Ultra-550b-a55b"),
}

_DEFAULT_BASE_URL = os.environ.get("NEBIUS_API_BASE_URL", "https://api.tokenfactory.nebius.com/v1")

# Soft guidance only (AGENT_SPECS.md §0) — logged as a warning, never blocked,
# since Ultra's valid range genuinely spans two different callers' needs.
_TEMP_BOUNDS: dict[ModelName, tuple[float, float]] = {
    "nano": (0.1, 0.3),
    "ultra": (0.1, 0.5),
}

_RETRY_NUDGE = (
    "Your previous response was not valid JSON. Return ONLY valid JSON "
    "matching the schema, with no other text."
)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class NemotronCallError(Exception):
    """Raised when a call fails outright: HTTP/network error, unexpected
    response shape, or two consecutive malformed-JSON responses. Callers
    must fail the job with this — never guess at a result (AGENTS.md #1
    applies to model calls exactly as it does to sandbox runs)."""


@dataclass
class NemotronResult:
    parsed: dict
    raw_response: str  # exactly what the model returned (content or reasoning_content)
    attempts: int  # 1 or 2
    model_id: str
    latency_s: float


def _extract_json(text: str) -> dict:
    """Strict parse first; on failure, pull the first {...} block out of
    surrounding prose. Reasoning models routinely ignore "no other text"
    instructions even after being told twice — this gives the retry a real
    chance to succeed instead of failing on stray prose alone."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(text)
    if match:
        return json.loads(match.group(0))  # let this raise if still broken
    raise json.JSONDecodeError("no JSON object found in response", text, 0)


def _message_text(message: dict) -> str:
    """See module docstring's reasoning-model gotcha. Prefer `content`
    (the standard, non-reasoning-model shape); fall back to
    `reasoning_content` when `content` is empty."""
    content = (message.get("content") or "").strip()
    if content:
        return content
    return (message.get("reasoning_content") or "").strip()


def _call_once(
    *, client: httpx.Client, base_url: str, api_key: str, model_id: str,
    messages: list[dict], temperature: float, timeout_s: float,
) -> tuple[str, float]:
    """One raw HTTP round-trip. Returns (message_text, latency_s). Raises
    NemotronCallError on any HTTP/network failure or unexpected response
    shape — never returns partial data."""
    start = time.perf_counter()
    try:
        resp = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model_id, "temperature": temperature, "messages": messages},
            timeout=timeout_s,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise NemotronCallError(f"Nemotron API call failed: {e}") from e
    latency_s = time.perf_counter() - start
    body = resp.json()
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError) as e:
        raise NemotronCallError(f"unexpected Nemotron response shape: {body!r}") from e
    return _message_text(message), latency_s


def call_nemotron(
    *,
    job_id: str,
    step: str,
    model: ModelName,
    system_prompt: str,
    payload: dict,
    temperature: float,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_s: float = 60.0,
    client: httpx.Client | None = None,
) -> NemotronResult:
    """The one function every agent calls.

    job_id/step: for the required prompt+response logging — e.g.
        job_id="abc123", step="bisect:candidate=<sha>" or
        step="diagnose" or step="fix:attempt=2".
    system_prompt/payload: payload is JSON-encoded as the user message, per
        each agent's schema in AGENT_SPECS.md.
    client: inject an httpx.Client (e.g. with a MockTransport) for testing;
        a real one is created and closed automatically otherwise.
    """
    base_url = base_url or _DEFAULT_BASE_URL
    api_key = api_key or os.environ.get("NEBIUS_API_KEY")
    if not api_key:
        raise NemotronCallError("NEBIUS_API_KEY not set")
    model_id = _MODEL_IDS[model]

    lo, hi = _TEMP_BOUNDS[model]
    if not (lo <= temperature <= hi):
        log.warning(
            "nemotron.temperature_out_of_documented_range",
            model=model, temperature=temperature, expected_range=[lo, hi],
        )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload)},
    ]

    owns_client = client is None
    if owns_client:
        client = httpx.Client()
    try:
        for attempt in (1, 2):
            raw, latency_s = _call_once(
                client=client, base_url=base_url, api_key=api_key, model_id=model_id,
                messages=messages, temperature=temperature, timeout_s=timeout_s,
            )
            log.info(
                "nemotron.call",
                job_id=job_id, step=step, model=model, model_id=model_id,
                attempt=attempt, temperature=temperature, messages=messages,
                raw_response=raw, latency_s=round(latency_s, 3),
            )
            try:
                parsed = _extract_json(raw)
            except json.JSONDecodeError:
                if attempt == 1:
                    messages = messages + [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": _RETRY_NUDGE},
                    ]
                    continue
                log.error(
                    "nemotron.malformed_json_twice",
                    job_id=job_id, step=step, model=model, raw_response=raw,
                )
                raise NemotronCallError(
                    f"[{job_id}/{step}] Nemotron ({model}) returned non-JSON twice; "
                    "not guessing at a result. See nemotron.malformed_json_twice log above."
                )
            else:
                return NemotronResult(
                    parsed=parsed, raw_response=raw, attempts=attempt,
                    model_id=model_id, latency_s=latency_s,
                )
        raise NemotronCallError("unreachable")  # pragma: no cover
    finally:
        if owns_client:
            client.close()
