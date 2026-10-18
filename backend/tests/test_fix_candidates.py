# backend/tests/test_fix_candidates.py
"""Best-of-N fixes: several DIFFERENT candidate fixes per round, the best one that
actually verified is kept. Layer A is exercised with fakes (including real
threads and barriers to prove what runs concurrently); the end-to-end tests use
real git, real sandbox subprocesses and the offline stand-in for the model."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

import api
import fixer
import offline
from events import JobCancelled, Reporter
from fixer import (
    APPROACHES, FIXER_APPROACH_ADDENDUM, FIXER_SYSTEM_PROMPT, FixerError, FixProposal, PreviousAttemptResult,
    VerifyOutcome, run_fix_loop, run_fix_loop_live, run_fix_rounds,
)
from sandbox_client import SandboxError
from schema import InMemoryEventStore, InMemoryJobStore


def _patch(tag: str, lines: int = 1) -> str:
    body = "".join(f"+{tag}{i}\n" for i in range(lines))
    return f"--- a/f.py\n+++ b/f.py\n@@ -1 +1,{lines + 1} @@\n x\n{body}"


def prop(tag="a", lines=1):
    return FixProposal(patch=_patch(tag, lines), rationale=f"idea {tag}", confidence_will_resolve="medium")


OK = lambda score: VerifyOutcome(score_after=score, resolved=True)  # noqa: E731
NO = lambda score: VerifyOutcome(score_after=score, resolved=False)  # noqa: E731


def _rounds(propose, verify, **kw):
    kw.setdefault("before_score", 300.0)
    return run_fix_rounds(propose=propose, verify=verify, **kw)


class Sink:
    def __init__(self):
        self.events = []
        self.rep = Reporter("j", self.events.append, None)

    def kinds(self):
        return [e.kind for e in self.events]


# ---------------------------------------------------------------------------
# Layer A
# ---------------------------------------------------------------------------

def test_one_candidate_is_exactly_the_sequential_loop():
    seen = []

    def propose(rnd, c, prev):
        seen.append((rnd, c, prev))
        return prop("a")

    a = _rounds(propose, lambda p: OK(10.0), candidates=1)
    b = run_fix_loop(propose=lambda n, prev: prop("a"), verify=lambda p: OK(10.0), before_score=300.0)
    assert a == b and a.attempts[0].round == 0 and a.attempts[0].candidate == 0 and seen == [(1, 1, None)]


def test_the_best_verified_candidate_wins_and_is_marked():
    scores = {"a": 12.0, "b": 9.5, "c": 10.5}
    res = _rounds(lambda r, c, p: prop("abc"[c - 1]), lambda p: OK(scores[p.rationale[-1]]), candidates=3)
    assert res.resolved and res.after_score == 9.5 and res.final_patch == _patch("b")
    assert [a.attempt for a in res.attempts] == [1, 2, 3] and [a.candidate for a in res.attempts] == [1, 2, 3]
    assert all(a.round == 1 for a in res.attempts)
    assert [a.selected for a in res.attempts] == [False, True, False] and res.selected_attempt == 2


def test_losers_are_reported_first_and_the_winner_last():
    order = []
    scores = {1: 12.0, 2: 9.5, 3: 10.5}
    _rounds(lambda r, c, p: prop("abc"[c - 1]), lambda p: OK(scores[p.attempt]), candidates=3,
            on_attempt=lambda a: order.append(a.attempt))
    assert order == [1, 3, 2]  # so "the latest state" is always the chosen one


def test_a_tie_goes_to_the_smaller_patch():
    sizes = {1: 6, 2: 2, 3: 4}
    res = _rounds(lambda r, c, p: prop("abc"[c - 1], sizes[c]), lambda p: OK(10.0), candidates=3)
    assert res.selected_attempt == 2


def test_only_verified_candidates_can_win_however_fast_the_others_look():
    outs = {1: NO(1.0), 2: OK(50.0), 3: NO(2.0)}  # unverified ones have better-looking numbers
    res = _rounds(lambda r, c, p: prop("abc"[c - 1]), lambda p: outs[p.attempt], candidates=3)
    assert res.resolved and res.selected_attempt == 2 and res.after_score == 50.0


def test_nothing_verified_means_unresolved_and_the_next_round_learns_from_the_closest_miss():
    seen = []

    def propose(rnd, c, prev):
        seen.append((rnd, c, prev))
        return prop("abc"[c - 1])

    outs = {
        1: VerifyOutcome(score_after=40.0, resolved=False, perf_ok=False),
        2: VerifyOutcome(score_after=11.0, resolved=False, perf_ok=True, tests_passed=False, tests_note="broke test_x"),
        3: VerifyOutcome(score_after=5.0, resolved=False, note="did not apply"),  # crash: not a measurement
        4: NO(30.0), 5: NO(31.0), 6: NO(32.0), 7: NO(33.0), 8: NO(34.0), 9: NO(35.0),
    }
    res = _rounds(propose, lambda p: outs[p.attempt], candidates=3, max_rounds=3)
    assert not res.resolved and len(res.attempts) == 9 and res.selected_attempt is None
    assert not any(a.selected for a in res.attempts)
    round2_prev = [prev for rnd, c, prev in seen if rnd == 2]
    assert len(round2_prev) == 3 and all(p is not None for p in round2_prev)
    # fast-but-failed-the-tests (candidate 2) beats a slower pure miss and a crash
    assert round2_prev[0].patch_applied == _patch("b") and round2_prev[0].still_regressed is False
    assert round2_prev[0].note == "broke test_x"
    assert [rnd for rnd, c, prev in seen if rnd == 1 and prev is not None] == []
    # the reported patch is the closest miss, not just "the last one tried"
    assert res.final_patch in {a.patch for a in res.attempts}


def test_a_later_round_can_still_resolve():
    def verify(p):
        return OK(9.0) if p.attempt == 5 else NO(100.0)
    res = _rounds(lambda r, c, p: prop("abc"[c - 1]), verify, candidates=3, max_rounds=3)
    assert res.resolved and res.selected_attempt == 5 and len(res.attempts) == 6
    assert [a.round for a in res.attempts] == [1, 1, 1, 2, 2, 2]


def test_proposals_are_requested_concurrently():
    barrier = threading.Barrier(3, timeout=5)

    def propose(rnd, c, prev):
        barrier.wait()  # deadlocks (and fails) unless all three are in flight together
        return prop("abc"[c - 1])

    res = _rounds(propose, lambda p: OK(10.0), candidates=3, max_rounds=1)
    assert res.resolved


def test_verification_is_serial_unless_told_otherwise():
    live = {"now": 0, "max": 0}
    lock = threading.Lock()

    def verify(p):
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        time.sleep(0.05)
        with lock:
            live["now"] -= 1
        return OK(10.0)

    _rounds(lambda r, c, p: prop("abc"[c - 1]), verify, candidates=3, verify_parallelism=1)
    assert live["max"] == 1  # concurrent benchmarks on one machine would corrupt each other's timings


def test_verification_runs_side_by_side_when_isolated():
    barrier = threading.Barrier(3, timeout=5)

    def verify(p):
        barrier.wait()
        return OK(10.0)

    res = _rounds(lambda r, c, p: prop("abc"[c - 1]), verify, candidates=3, verify_parallelism=3)
    assert res.resolved


def test_a_candidate_that_cannot_be_produced_is_skipped_not_fatal():
    def propose(rnd, c, prev):
        if c == 2:
            raise FixerError("schema-invalid")
        return prop("abc"[c - 1])

    sink = Sink()
    res = _rounds(propose, lambda p: OK(10.0 + p.attempt), candidates=3, reporter=sink.rep)
    assert res.resolved and [a.candidate for a in res.attempts] == [1, 3]
    assert [a.attempt for a in res.attempts] == [1, 2]  # attempts are numbered over what actually ran
    assert "fix.candidate_failed" in sink.kinds()


def test_if_every_proposal_fails_the_error_surfaces():
    def propose(rnd, c, prev):
        raise FixerError(f"bad {c}")
    with pytest.raises(FixerError, match="bad 1"):
        _rounds(propose, lambda p: OK(1.0), candidates=3)


def test_cancellation_is_never_swallowed():
    def propose(rnd, c, prev):
        raise JobCancelled("stop")
    with pytest.raises(JobCancelled):
        _rounds(propose, lambda p: OK(1.0), candidates=3)


def test_an_infrastructure_failure_while_verifying_fails_loudly_after_the_round_settles():
    finished = []

    def verify(p):
        if p.attempt == 2:
            raise SandboxError("vm down")
        time.sleep(0.05)
        finished.append(p.attempt)
        return NO(100.0)

    with pytest.raises(SandboxError, match="vm down"):
        _rounds(lambda r, c, p: prop("abc"[c - 1]), verify, candidates=3, verify_parallelism=3)
    assert sorted(finished) == [1, 3]  # nothing left running in the background


def test_events_pair_up_and_say_which_candidate_won():
    sink = Sink()
    scores = {1: 12.0, 2: 9.5, 3: 10.5}
    _rounds(lambda r, c, p: prop("abc"[c - 1]), lambda p: OK(scores[p.attempt]), candidates=3, reporter=sink.rep)
    k = sink.kinds()
    assert k.count("fixround.start") == k.count("fixround.end") == 1
    assert k.count("fix.start") == k.count("fix.end") == 3 and k.count("patch.built") == 3
    sel = next(e for e in sink.events if e.kind == "fix.selected")
    assert sel.data["candidate"] == 2 and sel.data["verified"] == 3 and "best of 3" in sel.message
    assert k.index("fixround.start") < k.index("fix.start") < k.index("fix.selected") < k.index("fixround.end")


def test_candidate_count_is_capped():
    seen = []

    def propose(rnd, c, prev):
        seen.append(c)
        return prop("abcdefgh"[c - 1])

    _rounds(propose, lambda p: OK(10.0), candidates=50, max_rounds=1)
    assert sorted(seen) == list(range(1, fixer.MAX_CANDIDATES + 1))


# ---------------------------------------------------------------------------
# Layer B — what the model is actually asked
# ---------------------------------------------------------------------------

class _Res:
    def __init__(self, parsed):
        self.parsed = parsed


def _fake_ultra(calls):
    def call(*, job_id, step, model, system_prompt, payload, temperature, **kw):
        calls.append({"step": step, "prompt": system_prompt, "payload": payload, "temperature": temperature})
        return _Res({"patch": _patch("m"), "rationale": "r", "confidence_will_resolve": "high"})
    return call


DIAG = {"category": "n_plus_one", "explanation": "x", "cited_lines": ["y"]}


def test_each_candidate_gets_its_own_approach_and_single_candidate_calls_are_untouched(monkeypatch):
    calls = []
    monkeypatch.setattr(fixer, "call_nemotron", _fake_ultra(calls))
    run_fix_loop_live(job_id="j", diagnosis=DIAG, full_file_contents="# FILE: f.py\nx\n",
                      verify=lambda p: NO(100.0), before_score=300.0, max_attempts=1, candidates=3)
    assert len(calls) == 3
    assert sorted(c["payload"]["approach"] for c in calls) == sorted(APPROACHES[:3])
    assert all(c["prompt"] == FIXER_SYSTEM_PROMPT + FIXER_APPROACH_ADDENDUM for c in calls)
    assert sorted(c["step"] for c in calls) == [f"fix:attempt=1:candidate={i}" for i in (1, 2, 3)]
    assert all(c["temperature"] == fixer.FIXER_TEMPERATURE for c in calls)  # diversity comes from approaches, not heat

    calls.clear()
    run_fix_loop_live(job_id="j", diagnosis=DIAG, full_file_contents="# FILE: f.py\nx\n",
                      verify=lambda p: OK(1.0), before_score=300.0, max_attempts=1)
    assert len(calls) == 1 and "approach" not in calls[0]["payload"]
    assert calls[0]["prompt"] == FIXER_SYSTEM_PROMPT and calls[0]["step"] == "fix:attempt=1"


def test_approaches_are_distinct_and_the_first_is_the_plain_minimal_fix():
    assert len(set(APPROACHES)) == len(APPROACHES) >= fixer.MAX_CANDIDATES
    assert "smallest" in APPROACHES[0]


def test_retry_feedback_reaches_every_candidate_of_the_next_round(monkeypatch):
    calls = []
    monkeypatch.setattr(fixer, "call_nemotron", _fake_ultra(calls))
    run_fix_loop_live(job_id="j", diagnosis=DIAG, full_file_contents="# FILE: f.py\nx\n",
                      verify=lambda p: NO(100.0), before_score=300.0, max_attempts=2, candidates=2)
    first, second = calls[:2], calls[2:]
    assert all(c["payload"]["previous_attempt_result"] is None for c in first)
    assert all(c["payload"]["previous_attempt_result"]["still_regressed"] is True for c in second)
    assert all(c["payload"]["attempt_number"] == 2 for c in second)


# ---------------------------------------------------------------------------
# Resolution of the knob
# ---------------------------------------------------------------------------

def test_candidate_count_resolution(monkeypatch):
    monkeypatch.setattr(api, "_FIX_CANDIDATES", "auto")
    assert api._resolve_fix_candidates(True) == 3 and api._resolve_fix_candidates(False) == 1
    monkeypatch.setattr(api, "_FIX_CANDIDATES", "4")
    assert api._resolve_fix_candidates(True) == 4 and api._resolve_fix_candidates(False) == 4
    monkeypatch.setattr(api, "_FIX_CANDIDATES", "99")
    assert api._resolve_fix_candidates(False) == api._MAX_FIX_CANDIDATES
    for junk in ("0", "-2", "many"):
        monkeypatch.setattr(api, "_FIX_CANDIDATES", junk)
        assert api._resolve_fix_candidates(True) == 1


# ---------------------------------------------------------------------------
# End to end — real git, real sandbox runs, offline stand-in model
# ---------------------------------------------------------------------------

def _wait(client, job_id, timeout=180.0):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job_id)


@pytest.fixture
def client3(monkeypatch, tmp_path):
    monkeypatch.setenv("CULPRIT_OFFLINE", "1")
    monkeypatch.setattr(api, "_FIX_CANDIDATES", "3")
    app = api.create_app(store=InMemoryJobStore(), event_store=InMemoryEventStore(), data_dir=tmp_path)
    return TestClient(app)


def test_demo_with_three_candidates_keeps_exactly_one_verified_winner(client3):
    jid = client3.post("/demo").json()["job_id"]
    job = _wait(client3, jid)
    assert job["status"] == "done" and job["final_result"] == "resolved" and job["fix"]["verified"] is True

    atts = job["fix_attempts"]
    assert [a["candidate"] for a in atts] == [1, 2, 3] and all(a["round"] == 1 for a in atts)
    selected = [a for a in atts if a["selected"]]
    assert len(selected) == 1
    winner = selected[0]
    resolved = [a for a in atts if a["resolved"]]
    assert winner["resolved"] and winner["score_after"] == min(a["score_after"] for a in resolved)
    assert job["fix"]["patch_diff"] == winner["patch"] and job["fix"]["after_score"] == winner["score_after"]
    assert job["fix"]["after_score"] < job["fix"]["before_score"] * 1.15

    evs = client3.get(f"/jobs/{jid}/events?limit=2000").json()["events"]
    kinds = [e["kind"] for e in evs]
    assert kinds.count("fixround.start") == kinds.count("fixround.end") == 1 and kinds.count("fix.selected") == 1
    assert kinds.count("fix.start") == kinds.count("fix.end") == 3
    # three Ultra proposals + diagnose + guard test; each with its own approach in the recorded request
    reqs = [e["data"]["request"] for e in evs if e["kind"] == "model.start" and "approach" in (e["data"].get("request") or {}).get("payload", {})]
    assert len(reqs) == 3 and len({r["payload"]["approach"] for r in reqs}) == 3
    # the regression test was written for the WINNING patch
    assert job["guard_test"]["status"] == "verified"
    assert client3.get("/config").json()["fix_candidates"] == 3
    md = client3.get(f"/jobs/{jid}/report.md").text
    assert "Candidates tried" in md and md.count("| verified |") == len(resolved) and md.count("✅") >= 1


def test_a_failing_candidate_does_not_stop_the_good_one_from_winning(monkeypatch, client3):
    real = offline._fixer

    def mixed(payload):
        if payload.get("approach") == APPROACHES[0]:  # candidate 1 proposes an edit that cannot match
            return {"edits": [{"file": "app/summary.py", "old": "THIS TEXT IS NOT IN THE FILE", "new": "x"}],
                    "rationale": "bad", "confidence_will_resolve": "low"}
        return real(payload)

    monkeypatch.setattr(offline, "_fixer", mixed)
    jid = client3.post("/demo").json()["job_id"]
    job = _wait(client3, jid)
    assert job["status"] == "done" and job["final_result"] == "resolved"
    atts = {a["candidate"]: a for a in job["fix_attempts"]}
    assert atts[1]["resolved"] is False and atts[1]["selected"] is False and atts[1]["error"]  # recorded honestly, no score
    assert sum(a["selected"] for a in atts.values()) == 1 and (atts[2]["selected"] or atts[3]["selected"])


def test_when_no_candidate_works_it_is_reported_unresolved_with_every_attempt(monkeypatch, client3):
    monkeypatch.setattr(offline, "_fixer", lambda payload: {
        "edits": [{"file": "app/summary.py", "old": "NOPE", "new": "x"}], "rationale": "x", "confidence_will_resolve": "low"})
    jid = client3.post("/demo").json()["job_id"]
    job = _wait(client3, jid)
    assert job["status"] == "done" and job["final_result"] == "unresolved_diagnosis_only"
    assert len(job["fix_attempts"]) == 9 and not any(a["selected"] for a in job["fix_attempts"])
    assert job["fix"]["verified"] is False and job["guard_test"] is None


def test_the_default_locally_is_still_the_original_sequential_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("CULPRIT_OFFLINE", "1")
    monkeypatch.setattr(api, "_FIX_CANDIDATES", "auto")
    c = TestClient(api.create_app(store=InMemoryJobStore(), event_store=InMemoryEventStore(), data_dir=tmp_path))
    jid = c.post("/demo").json()["job_id"]
    job = _wait(c, jid)
    assert job["final_result"] == "resolved" and len(job["fix_attempts"]) == 1
    a = job["fix_attempts"][0]
    assert a["round"] is None and a["candidate"] is None and a["selected"] is True
    kinds = [e["kind"] for e in c.get(f"/jobs/{jid}/events?limit=2000").json()["events"]]
    assert "fixround.start" not in kinds and "fix.selected" not in kinds
    assert "Candidates tried" not in c.get(f"/jobs/{jid}/report.md").text
    assert c.get("/config").json()["fix_candidates"] == 1
