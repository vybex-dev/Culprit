# backend/tests/test_patch_review.py
"""
The patch reviewer (reviewer.py): rules in pure code + an ADVISORY Nano screen.

What must hold:
  - the test-file rule is HARD (rejected before a sandbox run, reason fed back);
  - editing the benchmark script is FLAGGED, never refused;
  - Nano's opinion never changes "verified" and never picks the winner;
  - a Nano failure is "no review", not approval and not rejection.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import api
import offline
import reviewer
from schema import InMemoryEventStore, InMemoryJobStore


def _diff(path: str, old: str = "x = 1", new: str = "x = 2") -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"


# ---------------------------------------------------------------------------
# Layer A — pure rules
# ---------------------------------------------------------------------------

def test_patched_paths_strips_prefixes_and_dev_null():
    patch = _diff("app/a.py") + "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+y\n"
    assert reviewer.patched_paths(patch) == ["app/a.py", "new.py"]
    assert reviewer.patched_paths("") == []


def test_benchmark_targets_handles_scripts_and_modules():
    assert reviewer.benchmark_targets("python bench.py") == {"bench.py"}
    assert reviewer.benchmark_targets("python ./perf/run.py --n 5") == {"perf/run.py"}
    assert reviewer.benchmark_targets("python -m pkg.bench") == {"pkg/bench.py", "pkg/bench/__main__.py"}
    assert reviewer.benchmark_targets("make bench") == set()


def test_tests_in_command():
    assert reviewer.tests_in_command("pytest tests/unit -q") == {"tests/unit"}
    assert reviewer.tests_in_command("python -m unittest pkg.test_x") == {"pkg/test_x.py"}
    assert reviewer.tests_in_command("python -m unittest tests/test_summary.py") == {"tests/test_summary.py"}


def test_test_files_are_forbidden_only_when_the_job_has_a_test_command():
    patch = _diff("tests/test_summary.py") + _diff("app/summary.py")
    assert reviewer.forbidden_edits(patch, test_command=None) == []
    bad = reviewer.forbidden_edits(patch, test_command="python -m unittest tests/test_summary.py")
    assert [p for p, _ in bad] == ["tests/test_summary.py"]
    for name in ("pkg/test_a.py", "pkg/a_test.py", "conftest.py", "test/x.py", "tests/sub/helper.py"):
        assert reviewer.forbidden_edits(_diff(name), test_command="pytest")
    assert reviewer.forbidden_edits(_diff("app/summary.py"), test_command="pytest") == []


def test_a_test_path_named_in_the_command_is_forbidden_even_with_an_odd_name():
    assert reviewer.forbidden_edits(_diff("checks/suite.py"), test_command="python checks/suite.py")


def test_benchmark_edits_are_detected_but_not_forbidden():
    patch = _diff("bench.py") + _diff("app/summary.py")
    assert reviewer.benchmark_edits(patch, benchmark_command="python bench.py") == ["bench.py"]
    assert reviewer.benchmark_edits(_diff("app/summary.py"), benchmark_command="python bench.py") == []
    # not refused, with or without tests (only test files are)
    assert reviewer.forbidden_edits(patch, test_command=None) == []
    assert reviewer.forbidden_edits(patch, test_command="python -m unittest tests/test_summary.py") == []
    assert "benchmark script" in reviewer.benchmark_edit_concern(["bench.py"])


def test_rejection_note_names_the_file_and_tells_the_fixer_what_to_do():
    note = reviewer.rejection_note([("tests/test_a.py", "it is a test file")])
    assert "tests/test_a.py" in note and "rejected before it was run" in note and "under test" in note


@pytest.mark.parametrize("parsed,verdict,concerns", [
    ({"verdict": "ok", "concerns": ["ignored"]}, "ok", []),
    ({"verdict": "ok"}, "ok", []),
    ({"verdict": "suspicious", "concerns": ["a", "b", "c", "d"]}, "suspicious", ["a", "b", "c"]),
    ({"verdict": "suspicious", "concerns": []}, "suspicious", ["the reviewer flagged this patch but gave no reason"]),
    ({"verdict": "suspicious", "concerns": ["  ", "real"]}, "suspicious", ["real"]),
])
def test_validate_accepts_and_normalises(parsed, verdict, concerns):
    r = reviewer._validate(parsed)
    assert r.verdict == verdict and r.concerns == concerns


def test_validate_truncates_long_concerns():
    r = reviewer._validate({"verdict": "suspicious", "concerns": ["z" * 1000]})
    assert len(r.concerns[0]) == 300


@pytest.mark.parametrize("parsed", [
    {"verdict": "maybe"}, {}, {"verdict": None},
    {"verdict": "suspicious", "concerns": "one string"}, {"verdict": "suspicious", "concerns": [1, 2]},
])
def test_validate_rejects_unusable_responses(parsed):
    with pytest.raises(reviewer.ReviewError):
        reviewer._validate(parsed)


# ---------------------------------------------------------------------------
# Layer B — what Nano is asked
# ---------------------------------------------------------------------------

class _Res:
    def __init__(self, parsed):
        self.parsed = parsed


def test_review_patch_makes_one_nano_call_with_the_documented_payload(monkeypatch):
    seen = {}

    def fake(*, job_id, step, model, system_prompt, payload, temperature, **kw):
        seen.update(job_id=job_id, step=step, model=model, prompt=system_prompt, payload=payload, temperature=temperature)
        return _Res({"verdict": "suspicious", "concerns": ["hard-codes the result"]})

    monkeypatch.setattr(reviewer, "call_nemotron", fake)
    r = reviewer.review_patch(
        job_id="j1", diagnosis={"category": "n_plus_one", "explanation": "e", "cited_lines": ["l"], "diff": "BIG"},
        patch=_diff("app/a.py"), benchmark_command="python bench.py", has_tests=True, attempt=2,
    )
    assert r.verdict == "suspicious" and r.concerns == ["hard-codes the result"]
    assert seen["model"] == "nano" and seen["step"] == "review:attempt=2" and seen["job_id"] == "j1"
    assert seen["temperature"] == reviewer.REVIEW_TEMPERATURE == 0.1
    assert seen["prompt"] == reviewer.REVIEW_SYSTEM_PROMPT
    p = seen["payload"]
    assert p["patch_to_review"] == _diff("app/a.py") and p["benchmark_command"] == "python bench.py"
    assert p["project_has_tests"] is True
    assert set(p["diagnosis"]) == {"category", "explanation", "cited_lines"}  # the raw diff is not resent


def test_review_patch_propagates_an_invalid_response(monkeypatch):
    monkeypatch.setattr(reviewer, "call_nemotron", lambda **kw: _Res({"verdict": "dunno"}))
    with pytest.raises(reviewer.ReviewError):
        reviewer.review_patch(job_id="j", diagnosis={}, patch="", benchmark_command="python b.py", has_tests=False, attempt=1)


# ---------------------------------------------------------------------------
# Offline stand-in
# ---------------------------------------------------------------------------

def _review_offline(patch: str):
    return offline.offline_call("nano", {"patch_to_review": patch})


def test_offline_reviewer_passes_a_plain_revert():
    assert _review_offline(_diff("a.py", "    for x in xs:", "    ys = batch(xs)"))["verdict"] == "ok"


def test_offline_reviewer_flags_the_usual_cheats():
    const = _review_offline(_diff("a.py", "    return compute(x)", "    return 42"))
    assert const["verdict"] == "suspicious" and "hard-coded constant" in const["concerns"][0]
    assert "[offline stand-in reviewer" in const["concerns"][0]
    assert _review_offline(_diff("a.py", "    assert total == n", "    pass"))["verdict"] == "suspicious"
    assert _review_offline(_diff("a.py", "    raise ValueError(x)", "    y = 1"))["verdict"] == "suspicious"
    swallow = _review_offline(_diff("a.py", "    work()", "    except:"))
    assert swallow["verdict"] == "suspicious"


def test_offline_nano_bisect_verdicts_are_unaffected():
    out = offline.offline_call("nano", {"candidate_scores": [10, 10.1, 9.9, 10, 10.05], "baseline_score": 10,
                                        "regression_threshold_pct": 15})
    assert "verdict" in out and "patch_to_review" not in out


# ---------------------------------------------------------------------------
# End to end — real git, real sandbox runs, offline stand-in models
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
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CULPRIT_OFFLINE", "1")
    monkeypatch.setattr(api, "_FIX_CANDIDATES", "1")
    app = api.create_app(store=InMemoryJobStore(), event_store=InMemoryEventStore(), data_dir=tmp_path)
    return TestClient(app)


@pytest.fixture
def fixer_sees_everything(monkeypatch):
    """The Fixer is normally shown only the files the guilty commit changed, so it can't even
    build an edit to bench.py or the tests. A model may still name them when it can see them
    (e.g. a diff touching those files), so let it see them and prove the rules hold."""
    real = api._concat_file_contents_at_commit
    monkeypatch.setattr(
        api, "_concat_file_contents_at_commit",
        lambda repo, sha, paths: real(repo, sha, list(paths) + ["bench.py", "tests/test_summary.py"]),
    )


def _events(client, jid):
    return client.get(f"/jobs/{jid}/events?limit=2000").json()["events"]


def test_demo_attempt_gets_an_ok_review_event_and_no_warning(client):
    jid = client.post("/demo").json()["job_id"]
    job = _wait(client, jid)
    assert job["final_result"] == "resolved" and job["fix"]["verified"] is True
    assert job["fix_attempts"][0]["review"] == {"verdict": "ok", "concerns": []}
    evs = _events(client, jid)
    res = [e for e in evs if e["kind"] == "review.result"]
    assert len(res) == 1 and res[0]["data"]["verdict"] == "ok"
    nano_review_calls = [e for e in evs if e["kind"] == "model.start" and "patch_to_review" in (e["data"].get("request") or {}).get("payload", {})]
    assert len(nano_review_calls) == 1
    md = client.get(f"/jobs/{jid}/report.md").text
    assert "no red flags" in md


def test_a_suspicious_review_never_gates_or_unverifies_the_fix(monkeypatch, client):
    monkeypatch.setattr(offline, "_patch_review", lambda payload: {"verdict": "suspicious", "concerns": ["looks tuned to the benchmark"]})
    jid = client.post("/demo").json()["job_id"]
    job = _wait(client, jid)
    assert job["status"] == "done" and job["final_result"] == "resolved" and job["fix"]["verified"] is True
    a = job["fix_attempts"][0]
    assert a["resolved"] is True and a["review"] == {"verdict": "suspicious", "concerns": ["looks tuned to the benchmark"]}
    assert any(e["kind"] == "review.result" and e["data"]["verdict"] == "suspicious" for e in _events(client, jid))
    md = client.get(f"/jobs/{jid}/report.md").text
    assert "looks tuned to the benchmark" in md and "⚠️" in md


def test_a_failing_reviewer_means_no_review_not_a_failed_attempt(monkeypatch, client):
    def boom(payload):
        raise RuntimeError("nano down")

    monkeypatch.setattr(offline, "_patch_review", boom)
    jid = client.post("/demo").json()["job_id"]
    job = _wait(client, jid)
    assert job["final_result"] == "resolved" and job["fix"]["verified"] is True
    assert job["fix_attempts"][0]["review"] is None
    assert any(e["kind"] == "review.unavailable" for e in _events(client, jid))
    assert not any(e["kind"] == "review.result" for e in _events(client, jid))


def test_review_can_be_switched_off_without_touching_the_rules(monkeypatch, client):
    monkeypatch.setattr(api, "_PATCH_REVIEW_ENABLED", False)
    jid = client.post("/demo").json()["job_id"]
    job = _wait(client, jid)
    assert job["final_result"] == "resolved" and job["fix_attempts"][0]["review"] is None
    assert not any(e["kind"] in ("review.result", "review.unavailable") for e in _events(client, jid))


def test_a_patch_that_edits_a_test_file_is_rejected_without_a_sandbox_run(monkeypatch, client, fixer_sees_everything):
    real = offline._fixer

    def meddling(payload):
        out = real(payload)
        out["edits"].append({"file": "tests/test_summary.py", "old": "import unittest", "new": "import unittest  # edited"})
        return out

    monkeypatch.setattr(offline, "_fixer", meddling)
    jid = client.post("/demo").json()["job_id"]
    job = _wait(client, jid)
    # every attempt is the same meddling patch → none may be verified
    assert job["final_result"] == "unresolved_diagnosis_only" and job["fix"]["verified"] is False
    assert job["fix_attempts"] and all(not a["resolved"] for a in job["fix_attempts"])
    assert all("tests/test_summary.py" in (a["error"] or "") for a in job["fix_attempts"])
    evs = _events(client, jid)
    assert any(e["kind"] == "patch.rejected" for e in evs)


def test_a_patch_that_edits_the_benchmark_is_flagged_not_refused(monkeypatch, client, fixer_sees_everything):
    real = offline._fixer

    def touches_bench(payload):
        out = real(payload)
        out["edits"].append({"file": "bench.py", "old": "# warm-up (imports, first-call costs)",
                             "new": "# warm-up (imports, first-call costs) — edited"})
        return out

    monkeypatch.setattr(offline, "_fixer", touches_bench)
    jid = client.post("/demo").json()["job_id"]
    job = _wait(client, jid)
    assert job["final_result"] == "resolved" and job["fix"]["verified"] is True  # judged by the sandbox, as ever
    rv = job["fix_attempts"][0]["review"]
    assert rv["verdict"] == "suspicious" and "benchmark script" in rv["concerns"][0]
    assert any(e["kind"] == "review.rule" for e in _events(client, jid))
    assert "benchmark script" in client.get(f"/jobs/{jid}/report.md").text
