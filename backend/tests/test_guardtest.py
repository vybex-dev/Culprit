# backend/tests/test_guardtest.py
"""The regression-guard test: a test for the fix is offered ONLY if the sandbox
proved it (red on the regressed code with an assertion, green with the fix).
Layer A is exercised with fake runners; the end-to-end tests use real git, real
sandbox subprocesses and the offline stand-in for the model."""

import subprocess
import sys
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

import api
import guardtest
import offline
from guardtest import (
    GUARD_TEST_FILE, GuardProposal, check_test_source, read_unittest_output, run_guard_loop,
)
from patcher import PatchBuildError, new_file_patch
from sandbox_client import SandboxError
from schema import InMemoryEventStore, InMemoryJobStore

GOOD = textwrap.dedent('''
    import unittest

    class T(unittest.TestCase):
        def test_it(self):
            self.assertLessEqual(3, 5)
''')


class R:  # a SuiteResult-alike
    def __init__(self, passed, out, exit_code=None, timed_out=False):
        self.passed, self.output_tail, self.timed_out = passed, out, timed_out
        self.exit_code = exit_code if exit_code is not None else (0 if passed else 1)


PASS = R(True, "Ran 1 test in 0.001s\n\nOK")
FAIL = R(False, "FAIL: test_it\nAssertionError\n\nRan 1 test in 0.001s\n\nFAILED (failures=1)")
CRASH = R(False, "ERROR: test_it\nImportError\n\nRan 1 test in 0.001s\n\nFAILED (errors=1)")


def _unittest_output(body: str) -> tuple[str, int]:
    """Real `python -m unittest` output for a real test module."""
    import os, tempfile
    d = tempfile.mkdtemp()
    open(os.path.join(d, "test_x.py"), "w").write(body)
    p = subprocess.run([sys.executable, "-m", "unittest", "test_x", "-v"], cwd=d, capture_output=True, text=True)
    return p.stdout + p.stderr, p.returncode


# ---------------------------------------------------------------------------
# Layer A
# ---------------------------------------------------------------------------

def test_source_checks_reject_what_makes_a_regression_test_worthless():
    assert check_test_source(GOOD) is None
    assert "empty" in check_test_source("  \n")
    assert "too long" in check_test_source("# x\n" * 10_000)
    assert "not valid Python" in check_test_source("def (:\n")
    assert "TestCase" in check_test_source("def test_x():\n    assert True\n")
    for timing in ("import time\nt=time.perf_counter()", "import timeit", "import time\nx=time.time()"):
        assert "wall-clock" in check_test_source(GOOD + "\n" + timing + "\n")
    assert "source text" in check_test_source(GOOD + "\nimport inspect\ninspect.getsource(len)\n")
    assert "source text" in check_test_source(GOOD + "\nimport ast\nast.parse('x')\n")
    assert check_test_source(GOOD + "\nimport time\ntime.sleep(0)\n") is None  # sleeping isn't measuring


def test_unittest_output_distinguishes_a_failed_assertion_from_a_crash_from_nothing_ran():
    ok_out, ok_rc = _unittest_output(GOOD.replace("test_it", "test_ok"))
    v = read_unittest_output(ok_out, exit_code=ok_rc)
    assert v.ok and v.ran == 1 and v.failures == 0 and v.errors == 0

    out, rc = _unittest_output("import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(1, 2)\n")
    v = read_unittest_output(out, exit_code=rc)
    assert not v.ok and v.failures == 1 and v.errors == 0

    out, rc = _unittest_output("import unittest\nimport no_such_module_xyz\n")
    v = read_unittest_output(out, exit_code=rc)
    assert not v.ok and v.ran in (0, 1) and (v.errors >= 1 or v.ran == 0)

    out, rc = _unittest_output("import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        raise RuntimeError('x')\n")
    v = read_unittest_output(out, exit_code=rc)
    assert not v.ok and v.errors == 1 and v.failures == 0

    out, rc = _unittest_output("x = 1\n")
    v = read_unittest_output(out, exit_code=rc)
    assert not v.ok and v.ran == 0
    assert not read_unittest_output("", exit_code=0).ok  # garbled output is never read as success


def test_benchmark_source_finds_the_script_and_stays_optional():
    assert guardtest.benchmark_source("python bench.py --n 5", lambda p: f"src of {p}") == "src of bench.py"
    assert guardtest.benchmark_source("python ../evil.py", lambda p: "x") is None
    assert guardtest.benchmark_source("python /etc/x.py", lambda p: "x") is None
    assert guardtest.benchmark_source("make bench", lambda p: "x") is None

    def boom(path):
        raise ValueError("no such file")
    assert guardtest.benchmark_source("python bench.py", boom) is None
    assert len(guardtest.benchmark_source("python b.py", lambda p: "x" * 100_000)) == guardtest.MAX_BENCH_SOURCE_CHARS


def _loop(proposals, without, with_fix, good=None, **kw):
    """Drive run_guard_loop with scripted proposals and run results."""
    seen = []
    ps, ws, fs = list(proposals), list(without), list(with_fix)

    def propose(n, previous):
        seen.append((n, previous))
        return ps.pop(0)

    res = run_guard_loop(
        propose=propose, run_without_fix=lambda c: _pop(ws), run_with_fix=lambda c: _pop(fs),
        run_on_good=(lambda c: _pop(list(good))) if good is not None else None, **kw,
    )
    return res, seen


def _pop(xs):
    x = xs.pop(0)
    if isinstance(x, Exception):
        raise x
    return x


def P(code=GOOD, asserts="pins the count"):
    return GuardProposal(code, asserts)


def test_a_test_that_is_red_before_and_green_after_is_verified():
    res, seen = _loop([P()], [FAIL], [PASS], good=[PASS])
    assert res.verified and res.test_code == GOOD and res.reason == ""
    assert res.fails_without_fix and res.passes_with_fix and res.passes_on_good is True
    assert [a.status for a in res.attempts] == ["verified"] and seen == [(1, None)]


def test_a_test_that_also_fails_on_the_last_good_commit_is_still_verified_but_flagged():
    res, _ = _loop([P()], [FAIL], [PASS], good=[FAIL])
    assert res.verified and res.passes_on_good is False


def test_good_commit_run_that_cannot_happen_is_just_unknown():
    res, _ = _loop([P()], [FAIL], [PASS], good=[SandboxError("vm down")])
    assert res.verified and res.passes_on_good is None


def test_a_test_that_passes_on_the_regression_is_never_shipped():
    res, seen = _loop([P(), P()], [PASS, PASS], [], max_attempts=2)
    assert not res.verified and res.test_code is None
    assert [a.status for a in res.attempts] == ["did_not_fail", "did_not_fail"]
    assert "PASSED on the regressed code" in res.reason
    assert seen[1][1] and "PASSED on the regressed code" in seen[1][1]["problem"]  # fed back to the model


def test_a_crash_is_not_a_failed_assertion():
    res, _ = _loop([P(), P()], [CRASH, CRASH], [])
    assert not res.verified and [a.status for a in res.attempts] == ["crashed", "crashed"]


def test_a_test_that_fails_with_the_fix_applied_is_never_shipped():
    res, _ = _loop([P(), P()], [FAIL, FAIL], [FAIL, FAIL])
    assert not res.verified and [a.status for a in res.attempts] == ["failed_with_fix"] * 2


def test_a_timeout_counts_against_the_test():
    res, _ = _loop([P(), P()], [R(False, "x", exit_code=None, timed_out=True)] * 2, [])
    assert not res.verified and res.attempts[0].status == "timed_out"


def test_the_retry_can_succeed_after_feedback():
    res, seen = _loop([P(), P(GOOD + "\n# v2\n")], [PASS, FAIL], [PASS])
    assert res.verified and res.test_code.endswith("# v2\n") and [a.status for a in res.attempts] == ["did_not_fail", "verified"]
    assert seen[1][0] == 2


def test_a_declined_test_stops_without_retrying():
    res, seen = _loop([GuardProposal("", "no countable property")], [], [])
    assert not res.verified and res.attempts[0].status == "declined" and len(seen) == 1
    assert "no countable property" in res.reason


def test_a_rejected_source_is_never_run():
    runs = []
    res = run_guard_loop(
        propose=lambda n, prev: P(GOOD + "\nimport timeit\n"),
        run_without_fix=lambda c: runs.append(c), run_with_fix=lambda c: runs.append(c), max_attempts=2,
    )
    assert not res.verified and runs == [] and [a.status for a in res.attempts] == ["rejected", "rejected"]


def test_a_sandbox_failure_is_not_a_verdict_and_stops_the_loop():
    res, seen = _loop([P(), P()], [SandboxError("vm down")], [])
    assert not res.verified and res.attempts[0].status == "infra" and len(seen) == 1
    assert "could not run" in res.reason
    res, _ = _loop([P()], [FAIL], [SandboxError("vm down")])
    assert not res.verified and res.attempts[0].status == "infra"


# ---------------------------------------------------------------------------
# new_file_patch — through real `git apply`
# ---------------------------------------------------------------------------

def _git_repo(tmp_path):
    def run(*a):
        subprocess.run(a, cwd=tmp_path, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@e.com")
    run("git", "config", "user.name", "T")
    (tmp_path / "f.txt").write_text("x\n")
    run("git", "add", ".")
    run("git", "commit", "-q", "-m", "i")
    return tmp_path


@pytest.mark.parametrize("content", [
    "print('hi')",                       # no trailing newline
    "a\n\n\nb\n",                        # blank lines
    "x = '+++ b/y'\n--- a/y\n@@ -1 +1 @@\n",  # looks like diff syntax
    "tab\there  \n  indented\n",
    "crlf\r\nline\r\n",                  # normalised to \n
])
def test_new_file_patch_applies_with_real_git_and_reproduces_the_content(tmp_path, content):
    repo = _git_repo(tmp_path)
    p = new_file_patch(GUARD_TEST_FILE, content)
    r = subprocess.run(["git", "apply", "-"], cwd=repo, input=p, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    want = content.replace("\r\n", "\n")
    want = want if want.endswith("\n") else want + "\n"
    assert (repo / GUARD_TEST_FILE).read_text() == want


def test_new_file_patch_refuses_paths_that_escape_and_empty_files():
    for bad in ("../x.py", "/etc/x.py", "a/../../x.py", ""):
        with pytest.raises(PatchBuildError):
            new_file_patch(bad, "x = 1\n")
    with pytest.raises(PatchBuildError):
        new_file_patch("t.py", "   \n")


def test_combined_fix_and_test_patch_applies_in_one_go(tmp_path):
    repo = _git_repo(tmp_path)
    fix = "--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-x\n+y\n"
    both = fix.rstrip("\n") + "\n" + new_file_patch(GUARD_TEST_FILE, GOOD)
    r = subprocess.run(["git", "apply", "-"], cwd=repo, input=both, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    assert (repo / "f.txt").read_text() == "y\n" and (repo / GUARD_TEST_FILE).exists()


# ---------------------------------------------------------------------------
# The offline stand-in writer
# ---------------------------------------------------------------------------

def _hot(func, file, b, a, project=True):
    return {"func": func, "file": file, "line": 1, "project": project, "calls_before": b, "calls_after": a}


def test_stand_in_pins_the_real_function_whose_call_count_exploded():
    out = offline.offline_call("ultra", {
        "test_file_name": GUARD_TEST_FILE, "benchmark_command": "python bench.py",
        "profile_hotspots": [_hot("app/x.py:<genexpr>", "app/x.py", 10, 5000), _hot("app/db.py:query", "app/db.py", 4, 244),
                             _hot("lib:other", "lib.py", 0, 900, project=False)],
    })
    assert check_test_source(out["test_code"]) is None
    assert "TARGET_NAME = 'query'" in out["test_code"] and "MAX_CALLS = 124" in out["test_code"]
    assert "offline stand-in" in out["asserts"]


def test_stand_in_declines_instead_of_inventing_a_test():
    for payload in (
        {"test_file_name": "t.py", "benchmark_command": "python bench.py"},  # no profile
        {"test_file_name": "t.py", "benchmark_command": "make bench", "profile_hotspots": [_hot("a.py:f", "a.py", 1, 90)]},
        {"test_file_name": "t.py", "benchmark_command": "python b.py", "profile_hotspots": [_hot("a.py:f", "a.py", 50, 60)]},
    ):
        out = offline.offline_call("ultra", payload)
        assert out["test_code"] == "" and out["asserts"]


# ---------------------------------------------------------------------------
# End to end — real git, real sandbox runs, offline stand-in model
# ---------------------------------------------------------------------------

def _wait(client, job_id, timeout=120.0):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job_id)


@pytest.fixture
def offline_client(monkeypatch, tmp_path):
    monkeypatch.setenv("CULPRIT_OFFLINE", "1")
    app = api.create_app(store=InMemoryJobStore(), event_store=InMemoryEventStore(), data_dir=tmp_path)
    return TestClient(app)


def test_demo_job_ships_a_test_that_was_proven_red_then_green(offline_client):
    jid = offline_client.post("/demo").json()["job_id"]
    job = _wait(offline_client, jid)
    assert job["status"] == "done" and job["final_result"] == "resolved"

    g = job["guard_test"]
    assert g["status"] == "verified" and g["file_name"] == GUARD_TEST_FILE
    assert g["fails_without_fix"] is True and g["passes_with_fix"] is True and g["passes_on_good"] is True
    assert g["test_code"] and "unittest" in g["test_code"] and g["reason"] is None
    assert [a["status"] for a in g["attempts"]] == ["verified"]

    kinds = [e["kind"] for e in offline_client.get(f"/jobs/{jid}/events?limit=2000").json()["events"]]
    assert kinds.index("fix.end") < kinds.index("guardtest.start") < kinds.index("guardtest.attempt") \
        < kinds.index("guardtest.end") < kinds.index("job.done")
    assert kinds.count("guardtest.start") == kinds.count("guardtest.end") == 1

    r = offline_client.get(f"/jobs/{jid}/guard-test.py")
    assert r.status_code == 200 and r.text == g["test_code"] + ("" if g["test_code"].endswith("\n") else "\n")
    assert GUARD_TEST_FILE in r.headers["content-disposition"]

    md = offline_client.get(f"/jobs/{jid}/report.md").text
    assert "Regression guard test" in md and "Proven in sandbox runs" in md and GUARD_TEST_FILE in md


def test_the_shipped_test_really_fails_on_the_regression_and_passes_on_the_fix(offline_client, tmp_path):
    """Independent re-check of the proof, outside the pipeline: take the artifacts the
    job produced and run them ourselves against the demo repo's two states."""
    from demo_repo import build_demo_repo
    jid = offline_client.post("/demo").json()["job_id"]
    job = _wait(offline_client, jid)
    repo = build_demo_repo(tmp_path / "again")
    guilty, fix = job["regression_commit"], job["fix"]["patch_diff"]
    test = job["guard_test"]["test_code"]
    # the demo repo is deterministic, so the commit shas match the job's
    def run(commit, patch_text):
        wt = tmp_path / f"wt_{commit[:6]}_{bool(patch_text)}"
        subprocess.run(["git", "worktree", "add", "--detach", str(wt), commit], cwd=repo.path, check=True, capture_output=True)
        (wt / GUARD_TEST_FILE).write_text(test)
        if patch_text:
            subprocess.run(["git", "apply", "-"], cwd=wt, input=patch_text, text=True, check=True, capture_output=True)
        return subprocess.run([sys.executable, "-m", "unittest", GUARD_TEST_FILE[:-3]], cwd=wt, capture_output=True, text=True)
    red = run(guilty, "")
    green = run(guilty, fix)
    assert red.returncode != 0 and "FAILED (failures=1)" in red.stderr
    assert green.returncode == 0


def test_no_guard_test_when_the_fix_did_not_verify(monkeypatch, offline_client):
    # a fixer that never helps → unresolved → the stage must not even start
    monkeypatch.setattr(offline, "_fixer", lambda payload: {
        "edits": [{"file": "", "old": "", "new": ""}], "rationale": "x", "confidence_will_resolve": "low"})
    jid = offline_client.post("/demo").json()["job_id"]
    job = _wait(offline_client, jid)
    assert job["final_result"] == "unresolved_diagnosis_only" and job["guard_test"] is None
    assert offline_client.get(f"/jobs/{jid}/guard-test.py").status_code == 404
    assert "guardtest.start" not in [e["kind"] for e in offline_client.get(f"/jobs/{jid}/events?limit=2000").json()["events"]]


def test_a_model_that_cannot_write_a_test_leaves_the_fix_intact_and_says_so(monkeypatch, offline_client):
    monkeypatch.setattr(offline, "_guard_test", lambda payload: {"test_code": "", "asserts": "nothing countable here"})
    jid = offline_client.post("/demo").json()["job_id"]
    job = _wait(offline_client, jid)
    assert job["status"] == "done" and job["final_result"] == "resolved" and job["fix"]["verified"] is True
    g = job["guard_test"]
    assert g["status"] == "not_verified" and g["test_code"] is None and "nothing countable" in g["reason"]
    assert offline_client.get(f"/jobs/{jid}/guard-test.py").status_code == 404
    assert "No regression test was shipped" in offline_client.get(f"/jobs/{jid}/report.md").text


def test_a_useless_test_is_discarded_not_shipped(monkeypatch, offline_client):
    always_green = GOOD  # asserts nothing about the regression → passes on the regressed code
    monkeypatch.setattr(offline, "_guard_test", lambda payload: {"test_code": always_green, "asserts": "trust me"})
    jid = offline_client.post("/demo").json()["job_id"]
    job = _wait(offline_client, jid)
    g = job["guard_test"]
    assert job["final_result"] == "resolved"  # the fix is still verified on its own evidence
    assert g["status"] == "not_verified" and g["test_code"] is None
    assert [a["status"] for a in g["attempts"]] == ["did_not_fail", "did_not_fail"]
    assert "would not have caught" in g["reason"]


def test_a_crashing_stage_never_fails_the_job(monkeypatch, offline_client):
    def boom(**kw):
        raise RuntimeError("model exploded")
    monkeypatch.setattr(guardtest, "propose_guard_test", boom)
    jid = offline_client.post("/demo").json()["job_id"]
    job = _wait(offline_client, jid)
    assert job["status"] == "done" and job["error"] is None and job["final_result"] == "resolved"
    assert job["guard_test"]["status"] == "not_verified" and "model exploded" in job["guard_test"]["reason"]


def test_the_stage_can_be_turned_off(monkeypatch, offline_client):
    monkeypatch.setattr(api, "_GUARD_TEST_ENABLED", False)
    jid = offline_client.post("/demo").json()["job_id"]
    job = _wait(offline_client, jid)
    assert job["final_result"] == "resolved" and job["guard_test"] is None
