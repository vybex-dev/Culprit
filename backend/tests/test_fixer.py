# FILE: backend/tests/test_fixer.py — place at this path in the Culprit
# repo (new file, sits alongside test_bisector.py etc.).
"""
Layer A (run_fix_loop) tests use plain fakes for `propose`/`verify` — no
Nemotron, no sandbox, mirroring test_bisector.py's Layer A tests for
evaluate_candidate()/bisect(). Layer B (propose_patch/run_fix_loop_live)
tests monkeypatch propose_patch at the module level, mirroring
test_bisector.py's Layer B test monkeypatching call_nemotron.
"""

import pytest

import fixer  # noqa: E402  (module import so monkeypatch.setattr works)
from fixer import (  # noqa: E402
    FixerError, FixProposal, PreviousAttemptResult, VerifyOutcome, run_fix_loop, run_fix_loop_live,
)
from models import NemotronResult  # noqa: E402


# ---------------------------------------------------------------------------
# Layer A: run_fix_loop() — pure, fully injectable
# ---------------------------------------------------------------------------

def test_resolves_on_first_attempt_no_second_call():
    propose_calls = []

    def propose(attempt_number, previous_attempt_result):
        propose_calls.append((attempt_number, previous_attempt_result))
        return FixProposal(patch="diff --git a b", rationale="batch the query", confidence_will_resolve="high")

    def verify(proposal):
        return VerifyOutcome(score_after=101.0, resolved=True)

    result = run_fix_loop(propose=propose, verify=verify, before_score=121.0)

    assert result.resolved is True
    assert len(result.attempts) == 1
    assert result.attempts[0] == fixer.FixAttemptRecord(
        attempt=1, patch="diff --git a b", rationale="batch the query", score_after=101.0, resolved=True,
    )
    assert result.final_patch == "diff --git a b"
    assert result.before_score == 121.0
    assert result.after_score == 101.0
    assert propose_calls == [(1, None)]  # no previous_attempt_result on attempt 1


def test_resolves_on_second_attempt_previous_result_threaded_through():
    seen_previous = []

    def propose(attempt_number, previous_attempt_result):
        seen_previous.append(previous_attempt_result)
        return FixProposal(patch=f"patch-{attempt_number}", rationale="r", confidence_will_resolve="medium")

    outcomes = iter([
        VerifyOutcome(score_after=118.0, resolved=False),
        VerifyOutcome(score_after=102.0, resolved=True),
    ])

    def verify(proposal):
        return next(outcomes)

    result = run_fix_loop(propose=propose, verify=verify, before_score=121.0)

    assert result.resolved is True
    assert len(result.attempts) == 2
    assert seen_previous[0] is None
    assert seen_previous[1] == PreviousAttemptResult(
        patch_applied="patch-1", score_after_fix=118.0, still_regressed=True,
    )
    assert result.after_score == 102.0


def test_never_resolves_stops_at_max_attempts_reports_honestly():
    propose_calls = []

    def propose(attempt_number, previous_attempt_result):
        propose_calls.append(attempt_number)
        return FixProposal(patch=f"patch-{attempt_number}", rationale="r", confidence_will_resolve="low")

    def verify(proposal):
        return VerifyOutcome(score_after=119.0, resolved=False)

    result = run_fix_loop(propose=propose, verify=verify, before_score=121.0, max_attempts=3)

    assert result.resolved is False  # never fabricated as resolved
    assert propose_calls == [1, 2, 3]  # hard cap respected, no silent 4th try
    assert len(result.attempts) == 3
    assert result.final_patch == "patch-3"
    assert result.after_score == 119.0


def test_max_attempts_is_configurable():
    calls = []

    def propose(attempt_number, previous_attempt_result):
        calls.append(attempt_number)
        return FixProposal(patch="p", rationale="r", confidence_will_resolve="low")

    def verify(proposal):
        return VerifyOutcome(score_after=119.0, resolved=False)

    run_fix_loop(propose=propose, verify=verify, before_score=100.0, max_attempts=1)
    assert calls == [1]


# ---------------------------------------------------------------------------
# propose_patch() schema validation
# ---------------------------------------------------------------------------

def _fake_call_nemotron(parsed: dict):
    def fake(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
        return NemotronResult(parsed=parsed, raw_response="{}", attempts=1, model_id="fake", latency_s=0.0)
    return fake


def test_propose_patch_happy_path(monkeypatch):
    parsed = {"patch": "diff --git a b", "rationale": "batches the query", "confidence_will_resolve": "high"}
    monkeypatch.setattr(fixer, "call_nemotron", _fake_call_nemotron(parsed))

    proposal = fixer.propose_patch(
        job_id="j1", diagnosis={"category": "n_plus_one"}, full_file_contents="def f(): pass",
        attempt_number=1,
    )
    assert proposal == FixProposal(patch="diff --git a b", rationale="batches the query", confidence_will_resolve="high")


def test_propose_patch_missing_field_raises(monkeypatch):
    parsed = {"patch": "x", "rationale": "y"}  # no confidence_will_resolve
    monkeypatch.setattr(fixer, "call_nemotron", _fake_call_nemotron(parsed))
    with pytest.raises(FixerError):
        fixer.propose_patch(job_id="j1", diagnosis={}, full_file_contents="", attempt_number=1)


def test_propose_patch_empty_patch_string_raises(monkeypatch):
    parsed = {"patch": "   ", "rationale": "y", "confidence_will_resolve": "high"}
    monkeypatch.setattr(fixer, "call_nemotron", _fake_call_nemotron(parsed))
    with pytest.raises(FixerError):
        fixer.propose_patch(job_id="j1", diagnosis={}, full_file_contents="", attempt_number=1)


def test_propose_patch_unknown_confidence_raises(monkeypatch):
    parsed = {"patch": "x", "rationale": "y", "confidence_will_resolve": "extremely-high"}
    monkeypatch.setattr(fixer, "call_nemotron", _fake_call_nemotron(parsed))
    with pytest.raises(FixerError):
        fixer.propose_patch(job_id="j1", diagnosis={}, full_file_contents="", attempt_number=1)


def test_propose_patch_sends_previous_attempt_result_shape(monkeypatch):
    seen_payload = {}

    def fake(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
        seen_payload.update(payload)
        return NemotronResult(parsed={"patch": "x", "rationale": "y", "confidence_will_resolve": "low"},
                               raw_response="{}", attempts=1, model_id="fake", latency_s=0.0)

    monkeypatch.setattr(fixer, "call_nemotron", fake)

    fixer.propose_patch(
        job_id="j1", diagnosis={"category": "lost_cache"}, full_file_contents="contents",
        attempt_number=2,
        previous_attempt_result=PreviousAttemptResult(patch_applied="p1", score_after_fix=118.0, still_regressed=True),
    )
    assert seen_payload["attempt_number"] == 2
    assert seen_payload["previous_attempt_result"] == {
        "patch_applied": "p1", "score_after_fix": 118.0, "still_regressed": True,
    }
    assert seen_payload["diagnosis"] == {"category": "lost_cache"}


def test_propose_patch_first_attempt_has_null_previous_result(monkeypatch):
    seen_payload = {}

    def fake(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
        seen_payload.update(payload)
        return NemotronResult(parsed={"patch": "x", "rationale": "y", "confidence_will_resolve": "low"},
                               raw_response="{}", attempts=1, model_id="fake", latency_s=0.0)

    monkeypatch.setattr(fixer, "call_nemotron", fake)
    fixer.propose_patch(job_id="j1", diagnosis={}, full_file_contents="c", attempt_number=1)
    assert seen_payload["previous_attempt_result"] is None


# ---------------------------------------------------------------------------
# Layer B: run_fix_loop_live() — real propose_patch wiring, faked verify
# ---------------------------------------------------------------------------

def test_run_fix_loop_live_wires_job_context_into_every_propose_call(monkeypatch):
    seen_calls = []

    def fake_propose_patch(*, job_id, diagnosis, full_file_contents, attempt_number, previous_attempt_result, temperature):
        seen_calls.append({
            "job_id": job_id, "diagnosis": diagnosis, "full_file_contents": full_file_contents,
            "attempt_number": attempt_number, "previous_attempt_result": previous_attempt_result,
        })
        return FixProposal(patch=f"patch-{attempt_number}", rationale="r", confidence_will_resolve="medium")

    monkeypatch.setattr(fixer, "propose_patch", fake_propose_patch)

    outcomes = iter([VerifyOutcome(score_after=115.0, resolved=False), VerifyOutcome(score_after=99.0, resolved=True)])

    def verify(proposal):
        return next(outcomes)

    result = run_fix_loop_live(
        job_id="job-42", diagnosis={"category": "blocking_call"}, full_file_contents="the file",
        verify=verify, before_score=121.0,
    )

    assert result.resolved is True
    assert len(seen_calls) == 2
    assert all(c["job_id"] == "job-42" for c in seen_calls)
    assert all(c["diagnosis"] == {"category": "blocking_call"} for c in seen_calls)
    assert all(c["full_file_contents"] == "the file" for c in seen_calls)
    assert seen_calls[0]["previous_attempt_result"] is None
    assert seen_calls[1]["previous_attempt_result"] == PreviousAttemptResult(
        patch_applied="patch-1", score_after_fix=115.0, still_regressed=True,
    )
