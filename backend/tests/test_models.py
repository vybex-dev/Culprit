# backend/tests/test_models.py
"""
No live NEBIUS_API_KEY in this environment, so these tests fake the HTTP
transport (httpx.MockTransport) rather than skip testing the wrapper
entirely. This proves the retry/logging/reasoning-content-fallback logic
works against controlled responses — it does NOT prove the real Nebius
Token Factory endpoint/model IDs/response shape match what's assumed here.
See the "Real API surface" warning at the top of models.py before wiring
in real credentials.
"""

import json

import httpx
import pytest
import structlog

from models import NemotronCallError, call_nemotron  # noqa: E402


def _client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _openai_response(content: str = "", reasoning_content: str = "") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"content": content, "reasoning_content": reasoning_content}}
            ]
        },
    )


def test_normal_content_field_parses_first_try():
    def handler(request):
        return _openai_response(content='{"verdict": "clean"}')

    result = call_nemotron(
        job_id="j1", step="bisect", model="nano",
        system_prompt="sys", payload={"a": 1}, temperature=0.2,
        api_key="fake", client=_client_for(handler),
    )
    assert result.parsed == {"verdict": "clean"}
    assert result.attempts == 1


def test_reasoning_content_fallback_when_content_empty():
    """The gotcha this module exists to handle: content empty, real answer
    sitting in reasoning_content instead."""
    def handler(request):
        return _openai_response(content="", reasoning_content='{"verdict": "regressed"}')

    result = call_nemotron(
        job_id="j1", step="bisect", model="nano",
        system_prompt="sys", payload={"a": 1}, temperature=0.2,
        api_key="fake", client=_client_for(handler),
    )
    assert result.parsed == {"verdict": "regressed"}


def test_json_embedded_in_reasoning_prose_is_extracted():
    prose = 'Let me think... the median is higher.\n{"verdict": "regressed", "median_score": 121.0}\nDone.'

    def handler(request):
        return _openai_response(content=prose)

    result = call_nemotron(
        job_id="j1", step="bisect", model="nano",
        system_prompt="sys", payload={"a": 1}, temperature=0.2,
        api_key="fake", client=_client_for(handler),
    )
    assert result.parsed["verdict"] == "regressed"


def test_malformed_then_valid_retries_once_with_conversation_context():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["messages"])
        if len(calls) == 1:
            return _openai_response(content="not json at all")
        return _openai_response(content='{"verdict": "clean"}')

    result = call_nemotron(
        job_id="j1", step="bisect", model="nano",
        system_prompt="sys", payload={"a": 1}, temperature=0.2,
        api_key="fake", client=_client_for(handler),
    )
    assert result.attempts == 2
    assert result.parsed == {"verdict": "clean"}
    # second call must carry the failed attempt + the corrective nudge,
    # not just repeat the original two messages verbatim
    assert len(calls[1]) == 4
    assert calls[1][2]["role"] == "assistant" and calls[1][2]["content"] == "not json at all"
    assert "not valid JSON" in calls[1][3]["content"]


def test_malformed_twice_raises_and_does_not_fabricate():
    def handler(request):
        return _openai_response(content="still not json")

    with pytest.raises(NemotronCallError):
        call_nemotron(
            job_id="j1", step="bisect", model="nano",
            system_prompt="sys", payload={"a": 1}, temperature=0.2,
            api_key="fake", client=_client_for(handler),
        )


def test_http_error_raises_nemotron_call_error():
    """A non-retryable client error (4xx) must fail immediately, no retries."""
    def handler(request):
        return httpx.Response(400, text="bad request")

    with pytest.raises(NemotronCallError):
        call_nemotron(
            job_id="j1", step="bisect", model="nano",
            system_prompt="sys", payload={"a": 1}, temperature=0.2,
            api_key="fake", client=_client_for(handler), retry_delays_s=(),
        )


def test_transient_5xx_retries_then_succeeds():
    """CODE_REVIEW_FINDINGS.md #20: a transient 500 must not burn the whole
    call — it should retry with backoff and succeed once the transient
    condition clears, exactly like a real flaky upstream would."""
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(500, text="internal error")
        return _openai_response(content='{"verdict": "clean"}')

    result = call_nemotron(
        job_id="j1", step="bisect", model="nano",
        system_prompt="sys", payload={"a": 1}, temperature=0.2,
        api_key="fake", client=_client_for(handler), retry_delays_s=(0.0, 0.0),
    )
    assert result.parsed == {"verdict": "clean"}
    assert len(calls) == 3


def test_transient_error_exhausted_retries_raises():
    def handler(request):
        return httpx.Response(503, text="unavailable")

    with pytest.raises(NemotronCallError):
        call_nemotron(
            job_id="j1", step="bisect", model="nano",
            system_prompt="sys", payload={"a": 1}, temperature=0.2,
            api_key="fake", client=_client_for(handler), retry_delays_s=(0.0, 0.0),
        )


def test_429_is_treated_as_retryable():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 2:
            return httpx.Response(429, text="rate limited")
        return _openai_response(content='{"ok": true}')

    result = call_nemotron(
        job_id="j1", step="bisect", model="nano",
        system_prompt="sys", payload={"a": 1}, temperature=0.2,
        api_key="fake", client=_client_for(handler), retry_delays_s=(0.0,),
    )
    assert result.parsed == {"ok": True}
    assert len(calls) == 2


def test_400_is_not_retried():
    """A 4xx (bad request/auth) will never succeed on retry — must fail on
    the first attempt, not waste the retry budget."""
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(NemotronCallError):
        call_nemotron(
            job_id="j1", step="bisect", model="nano",
            system_prompt="sys", payload={"a": 1}, temperature=0.2,
            api_key="fake", client=_client_for(handler), retry_delays_s=(0.0, 0.0),
        )
    assert len(calls) == 1


def test_missing_api_key_raises_without_making_a_call():
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _openai_response(content="{}")

    with pytest.raises(NemotronCallError):
        call_nemotron(
            job_id="j1", step="bisect", model="nano",
            system_prompt="sys", payload={}, temperature=0.2,
            api_key=None, client=_client_for(handler),
        )
    assert not called


def test_out_of_range_temperature_warns_but_still_succeeds():
    def handler(request):
        return _openai_response(content='{"ok": true}')

    with structlog.testing.capture_logs() as logs:
        result = call_nemotron(
            job_id="j1", step="bisect", model="nano",
            system_prompt="sys", payload={}, temperature=0.9,  # way above nano's 0.1-0.3
            api_key="fake", client=_client_for(handler),
        )
    assert result.parsed == {"ok": True}
    assert any(entry.get("event") == "nemotron.temperature_out_of_documented_range" for entry in logs)


def test_every_attempt_is_logged_with_job_id_and_step():
    def handler(request):
        return _openai_response(content='{"ok": true}')

    with structlog.testing.capture_logs() as logs:
        call_nemotron(
            job_id="job-42", step="fix:attempt=1", model="ultra",
            system_prompt="sys", payload={}, temperature=0.2,
            api_key="fake", client=_client_for(handler),
        )
    call_logs = [e for e in logs if e.get("event") == "nemotron.call"]
    assert len(call_logs) == 1
    assert call_logs[0]["job_id"] == "job-42"
    assert call_logs[0]["step"] == "fix:attempt=1"
