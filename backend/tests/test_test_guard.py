# backend/tests/test_test_guard.py
"""The correctness guard: a fix is only "verified" if the benchmark is back within
threshold AND (when the job has a test command) the tests are green on the patched
code. Layer A uses fakes; the sandbox and API tests use real git repos and real
subprocesses, like the rest of the suite."""

import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api
import offline
from fixer import FixProposal, PreviousAttemptResult, VerifyOutcome, run_fix_loop
from sandbox_client import LocalGitSandbox, Sandbox, SandboxError
from schema import InMemoryEventStore, InMemoryJobStore

TERMINAL = ("done", "failed", "cancelled")


def _wait(client, job_id, timeout=90.0):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/jobs/{job_id}").json()
        if j["status"] in TERMINAL:
            return j
        time.sleep(0.1)
    raise TimeoutError(job_id)


def _events(client, job_id):
    return client.get(f"/jobs/{job_id}/events?limit=2000").json()["events"]


# ---------------------------------------------------------------------------
# Layer A — the loop
# ---------------------------------------------------------------------------

def _proposer(seen):
    def propose(n, prev):
        seen.append(prev)
        return FixProposal(patch=f"patch-{n}", rationale="r", confidence_will_resolve="medium")
    return propose


def test_tests_failing_rejects_a_fast_patch_and_tells_the_model_to_keep_the_speedup():
    seen = []
    outcomes = iter([
        VerifyOutcome(score_after=10.0, resolved=False, perf_ok=True, tests_passed=False,
                      tests_note="BROKE THE TEST SUITE", test_output="1 failed"),
        VerifyOutcome(score_after=10.5, resolved=True, perf_ok=True, tests_passed=True),
    ])
    result = run_fix_loop(propose=_proposer(seen), verify=lambda p: next(outcomes), before_score=300.0)

    assert result.resolved is True and len(result.attempts) == 2
    first = result.attempts[0]
    assert first.resolved is False and first.tests_passed is False and first.test_output == "1 failed"
    assert first.error == ""  # the score IS a measurement; only the tests rejected it
    assert result.attempts[1].tests_passed is True
    # the retry is told it fixed the speed but broke the tests — not "still regressed"
    assert seen[1] == PreviousAttemptResult(
        patch_applied="patch-1", score_after_fix=10.0, still_regressed=False, note="BROKE THE TEST SUITE",
    )


def test_without_a_guard_the_outcome_is_unchanged():
    seen = []
    outcomes = iter([VerifyOutcome(score_after=200.0, resolved=False), VerifyOutcome(score_after=10.0, resolved=True)])
    result = run_fix_loop(propose=_proposer(seen), verify=lambda p: next(outcomes), before_score=300.0)
    assert result.attempts[0].tests_passed is None and result.attempts[1].tests_passed is None
    assert seen[1].still_regressed is True and seen[1].note == ""


# ---------------------------------------------------------------------------
# Sandbox.run_tests
# ---------------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def tiny_repo(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "check.py").write_text("from calc import add\nassert add(2, 2) == 4, 'add is broken'\nprint('ok')\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo, _git(repo, "rev-parse", "HEAD")


BREAKING_PATCH = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""


def test_run_tests_reports_pass_fail_and_patched(tiny_repo, tmp_path):
    repo, sha = tiny_repo
    sb = LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache"))
    seen = []
    sb.listener = lambda kind, data: seen.append((kind, data))

    ok = sb.run_tests(sha, "python check.py")
    assert ok.passed and ok.exit_code == 0 and not ok.patched and "ok" in ok.output_tail

    bad = sb.run_tests(sha, "python check.py", patch=BREAKING_PATCH)
    assert not bad.passed and bad.exit_code != 0 and bad.patched
    assert "add is broken" in bad.output_tail  # the failure text is kept for the UI / the model

    assert [k for k, _ in seen if k == "tests"] == ["tests", "tests"]


def test_run_tests_unappliable_patch_is_a_sandbox_error_not_a_pass(tiny_repo, tmp_path):
    repo, sha = tiny_repo
    sb = LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache"))
    with pytest.raises(SandboxError):
        sb.run_tests(sha, "python check.py", patch="not a diff\n")


def test_a_backend_without_test_support_refuses_instead_of_pretending():
    class Bare(Sandbox):
        def run_benchmark(self, *a, **k): ...
        def apply_patch_and_benchmark(self, *a, **k): ...

    with pytest.raises(SandboxError):
        Bare().run_tests("abc", "pytest")


# ---------------------------------------------------------------------------
# End to end, through the API (offline stand-in models, real sandbox + git)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CULPRIT_OFFLINE", "1")
    app = api.create_app(store=InMemoryJobStore(), event_store=InMemoryEventStore(), data_dir=tmp_path)
    return TestClient(app)


def test_demo_is_verified_on_speed_and_on_behaviour(client):
    job = _wait(client, client.post("/demo").json()["job_id"])
    assert job["status"] == "done" and job["final_result"] == "resolved"
    assert job["tests_guard"] == "active" and job["test_command"]
    assert job["fix"]["verified"] is True and job["fix"]["tests_verified"] is True
    assert job["fix_attempts"][-1]["tests_passed"] is True

    kinds = [e["kind"] for e in _events(client, job["job_id"])]
    assert "tests.end" in kinds and "tests.result" in kinds and "sandbox.tests" in kinds
    assert "tests passed" in client.get(f"/jobs/{job['job_id']}/report.md").text or "Behaviour preserved" in client.get(
        f"/jobs/{job['job_id']}/report.md").text


def test_a_fast_but_wrong_patch_is_rejected_then_the_retry_is_verified(monkeypatch, client):
    """First attempt: reverts the regression BUT also corrupts the result, so the
    benchmark is fast and the tests are red. It must NOT be called verified."""
    real_fixer, calls = offline._fixer, []

    def wrong_then_right(payload):
        calls.append(payload.get("attempt_number"))
        out = real_fixer(payload)
        if payload.get("attempt_number") == 1:
            # make the 'fixed' code return nothing for a known customer
            for e in out["edits"]:
                if e["file"].endswith("summary.py"):
                    e["new"] = e["new"].replace("return [serialize_order(o) for o in orders]", "return []")
        else:
            assert "TEST SUITE" in str(payload["previous_attempt_result"].get("note", "")).upper()
        return out

    monkeypatch.setattr(offline, "_fixer", wrong_then_right)
    job = _wait(client, client.post("/demo").json()["job_id"])

    assert job["status"] == "done", job.get("error")
    first = job["fix_attempts"][0]
    assert first["resolved"] is False and first["tests_passed"] is False
    assert first["error"] is None  # a real score, rejected by the tests
    assert job["final_result"] == "resolved" and job["fix"]["tests_verified"] is True
    assert calls[:2] == [1, 2]


def test_a_patch_that_only_ever_breaks_behaviour_is_never_called_verified(monkeypatch, client):
    real_fixer = offline._fixer

    def always_wrong(payload):
        out = real_fixer(payload)
        for e in out["edits"]:
            if e["file"].endswith("summary.py"):
                e["new"] = e["new"].replace("return [serialize_order(o) for o in orders]", "return []")
        return out

    monkeypatch.setattr(offline, "_fixer", always_wrong)
    job = _wait(client, client.post("/demo").json()["job_id"])
    assert job["status"] == "done"
    assert job["final_result"] == "unresolved_diagnosis_only"
    assert job["fix"]["verified"] is False and job["fix"]["tests_verified"] is False
    assert all(a["tests_passed"] is False for a in job["fix_attempts"])


def test_a_suite_that_is_already_red_cannot_gate_and_says_so(monkeypatch, client):
    """Guard is only trusted if green on the regressed commit; otherwise it's
    reported as unusable and the fix is verified on performance alone."""
    real = api.build_demo_repo

    def red_suite_demo(dest):
        repo = real(dest)
        repo.test_command = "python -c 'import sys; sys.exit(1)'"
        return repo

    monkeypatch.setattr(api, "build_demo_repo", red_suite_demo)
    job = _wait(client, client.post("/demo").json()["job_id"])
    assert job["status"] == "done" and job["tests_guard"] == "baseline_failing"
    assert job["fix"]["verified"] is True and job["fix"]["tests_verified"] is None
    assert "performance-only" in client.get(f"/jobs/{job['job_id']}/report.md").text


def test_no_test_command_behaves_exactly_as_before(tmp_path, client):
    """Plain /analyze without test_command: no guard, tests_verified stays None."""
    from demo_repo import build_demo_repo

    demo = build_demo_repo(tmp_path / "plain-demo")
    r = client.post("/analyze", json={"repo_url": demo.path, "benchmark_command": demo.benchmark_command,
                                      "commit_range": [demo.start_sha, demo.end_sha]})
    assert r.status_code == 200
    job = _wait(client, r.json()["job_id"])
    assert job["status"] == "done" and job["final_result"] == "resolved"
    assert job["test_command"] is None and job["tests_guard"] is None
    assert job["fix"]["verified"] is True and job["fix"]["tests_verified"] is None
    assert not any(e["kind"].startswith("tests.") for e in _events(client, job["job_id"]))


def test_blank_test_command_is_treated_as_none(client):
    # validation only: use a bad repo so no job work happens meaningfully
    r = client.post("/analyze", json={"repo_url": "https://example.invalid/x.git",
                                      "benchmark_command": "python bench.py", "test_command": "   "})
    assert r.status_code == 200
    job = client.get(f"/jobs/{r.json()['job_id']}").json()
    assert job["test_command"] is None


# ---------------------------------------------------------------------------
# Token Factory backend — mocked HTTP, but the real in-VM shell script runs
# ---------------------------------------------------------------------------

def test_token_factory_run_tests_pass_fail_and_patched(tmp_path):
    import sys
    from tests.test_token_factory_sandbox import _fake_api, _repo, _sb

    repo, sha = _repo(tmp_path)
    cap = {}
    ok = _sb(repo, _fake_api(cap)).run_tests(sha, f"{sys.executable} bench.py")
    assert ok.passed and ok.exit_code == 0 and "12.5" in ok.output_tail and not ok.patched
    assert cap["disposable"] is True and cap["env"]["CULPRIT_TEST"].endswith("bench.py")

    bad = _sb(repo, _fake_api({})).run_tests(sha, f"{sys.executable} -c 'import sys; print(\"boom\"); sys.exit(3)'")
    assert not bad.passed and bad.exit_code == 3 and "boom" in bad.output_tail  # a red suite is a result, not an error

    patch = subprocess.run(["sh", "-c", "sed -i 's/12.5/7.5/' bench.py && git diff && git checkout -q bench.py"],
                           cwd=repo, capture_output=True, text=True).stdout
    patched = _sb(repo, _fake_api({})).run_tests(sha, f"{sys.executable} bench.py", patch=patch)
    assert patched.passed and patched.patched and "7.5" in patched.output_tail


def test_token_factory_run_tests_infra_failure_raises(tmp_path):
    import sys
    from tests.test_token_factory_sandbox import _fake_api, _repo, _sb

    repo, sha = _repo(tmp_path)
    with pytest.raises(SandboxError):
        _sb(repo, _fake_api({}, fail=True)).run_tests(sha, f"{sys.executable} bench.py")
