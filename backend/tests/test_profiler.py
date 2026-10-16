# backend/tests/test_profiler.py
"""Measured evidence for the Diagnoser: cProfile on the last good and the guilty
commit, diffed. Real git repos and real subprocesses; only the model is the
labelled offline stand-in. Profiling is best-effort — it must never fail a job."""

import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

import api
import diagnoser
import offline
import profiler
from sandbox_client import BenchmarkRunError, LocalGitSandbox, ProfileRun, Sandbox, SandboxError
from schema import InMemoryEventStore, InMemoryJobStore

TERMINAL = ("done", "failed", "cancelled")


def _wait(client, job_id, timeout=120.0):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/jobs/{job_id}").json()
        if j["status"] in TERMINAL:
            return j
        time.sleep(0.1)
    raise TimeoutError(job_id)


def _events(client, job_id):
    return client.get(f"/jobs/{job_id}/events?limit=2000").json()["events"]


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def two_commit_repo(tmp_path):
    """bench.py loops calling fetch(): 3 times at the good commit, 30 at the bad one."""
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text(
        "def fetch(i):\n    return sum(range(2000))\n\n\ndef run(n):\n    return [fetch(i) for i in range(n)]\n"
    )
    (repo / "bench.py").write_text("import app\napp.run(3)\nprint(1.0)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "good")
    good = _git(repo, "rev-parse", "HEAD")
    (repo / "bench.py").write_text("import app\napp.run(30)\nprint(1.0)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "bad")
    return repo, good, _git(repo, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# profiler.py — pure
# ---------------------------------------------------------------------------

def test_summarize_refuses_to_invent_a_profile():
    with pytest.raises(ValueError):
        profiler.summarize([], root="/x")
    with pytest.raises(ValueError):
        profiler.summarize([b"not a profile"], root="/x")


def test_local_sandbox_profiles_without_touching_the_command(two_commit_repo, tmp_path):
    repo, good, bad = two_commit_repo
    sb = LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache"))
    runs = [sb.profile(sha, f"{sys.executable} bench.py") for sha in (good, bad)]
    assert all(r.blobs for r in runs)

    before = profiler.summarize(runs[0].blobs, root=runs[0].root)
    after = profiler.summarize(runs[1].blobs, root=runs[1].root)
    ev = profiler.build_evidence(before_commit=good, after_commit=bad, before=before, after=after)

    fetch = next(h for h in ev["hotspots"] if h["func"].endswith(":fetch"))
    assert (fetch["calls_before"], fetch["calls_after"]) == (3, 30)
    assert fetch["file"] == "app.py" and fetch["project"] is True  # paths are relative to the checkout
    assert ev["after_total_s"] > ev["before_total_s"]


def test_a_benchmark_that_is_not_python_yields_no_profile_not_a_fake_one(two_commit_repo, tmp_path):
    repo, good, _ = two_commit_repo
    run = LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache")).profile(good, "echo 1.0")
    assert run.blobs == []
    with pytest.raises(ValueError):
        profiler.summarize(run.blobs, root=run.root)


def test_a_crashing_benchmark_is_a_benchmark_error(two_commit_repo, tmp_path):
    repo, good, _ = two_commit_repo
    with pytest.raises(BenchmarkRunError):
        LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache")).profile(good, f"{sys.executable} -c 'raise SystemExit(2)'")


def test_a_backend_without_profiling_refuses():
    class Bare(Sandbox):
        def run_benchmark(self, *a, **k): ...
        def apply_patch_and_benchmark(self, *a, **k): ...

    with pytest.raises(SandboxError):
        Bare().profile("abc", "python bench.py")


def test_token_factory_profile_roundtrip(tmp_path):
    from tests.test_token_factory_sandbox import _fake_api, _sb

    repo = tmp_path / "tf"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a@b")
    _git(repo, "config", "user.name", "a")
    (repo / "bench.py").write_text("def f():\n    return sum(range(500))\nfor _ in range(7):\n    f()\nprint(2.5)\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "c1")
    sha = _git(repo, "rev-parse", "HEAD")

    cap = {}
    run = _sb(repo, _fake_api(cap)).profile(sha, f"{sys.executable} bench.py")
    assert isinstance(run, ProfileRun) and run.blobs and run.root == "/work"
    assert cap["disposable"] is True and "CULPRIT_SITECUSTOMIZE_B64" in cap["env"]
    summary = profiler.summarize(run.blobs, root="/work")
    # (the mock re-executes the script on every status poll, leaving one dump per execution;
    # the real API runs it once — either way each process contributes exactly 7 calls)
    assert any(f["calls"] == 7 * len(run.blobs) and f["func"].endswith(":f") for f in summary["functions"])


# ---------------------------------------------------------------------------
# End to end (offline stand-in models, real git + sandbox + profiling)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CULPRIT_OFFLINE", "1")
    app = api.create_app(store=InMemoryJobStore(), event_store=InMemoryEventStore(), data_dir=tmp_path)
    return TestClient(app)


def test_demo_job_carries_measured_evidence_into_state_diagnosis_and_report(monkeypatch, client):
    seen = {}
    real = diagnoser.call_nemotron

    def spy(**kw):
        seen[kw["step"]] = kw
        return real(**kw)

    monkeypatch.setattr(diagnoser, "call_nemotron", spy)
    job = _wait(client, client.post("/demo").json()["job_id"])
    assert job["status"] == "done" and job["final_result"] == "resolved", job.get("error")

    prof = job["profile"]
    assert prof and prof["before_commit"] != prof["after_commit"] == job["regression_commit"]
    q = next(h for h in prof["hotspots"] if h["func"].endswith(":query"))  # DB.query: one call per order now
    assert q["calls_after"] >= 10 * q["calls_before"] and q["project"] is True

    # the model was shown the evidence, with the instruction that explains it
    d = seen["diagnose"]
    assert d["payload"]["profile_evidence"]["hotspots"]
    assert diagnoser.PROFILE_EVIDENCE_ADDENDUM in d["system_prompt"]
    assert diagnoser.DIAGNOSER_SYSTEM_PROMPT in d["system_prompt"]

    kinds = [e["kind"] for e in _events(client, job["job_id"])]
    assert "profile.start" in kinds and "profile.end" in kinds
    assert "Measured evidence" in client.get(f"/jobs/{job['job_id']}/report.md").text


def test_a_profile_failure_never_fails_the_job(monkeypatch, client):
    def boom(self, sha, cmd):
        raise SandboxError("profiling is not available here")

    monkeypatch.setattr(LocalGitSandbox, "profile", boom)
    seen = {}
    real = diagnoser.call_nemotron
    monkeypatch.setattr(diagnoser, "call_nemotron", lambda **kw: (seen.update(kw), real(**kw))[1])

    job = _wait(client, client.post("/demo").json()["job_id"])
    assert job["status"] == "done" and job["final_result"] == "resolved"
    assert job["profile"] is None
    assert "profile_evidence" not in seen["payload"]
    assert diagnoser.PROFILE_EVIDENCE_ADDENDUM not in seen["system_prompt"]  # prompt untouched without evidence
    end = next(e for e in _events(client, job["job_id"]) if e["kind"] == "profile.end")
    assert end["data"]["skipped"] is True and "profiling is not available" in end["data"]["reason"]


def test_profiling_can_be_switched_off(monkeypatch, client):
    monkeypatch.setattr(api, "_PROFILE_ENABLED", False)
    job = _wait(client, client.post("/demo").json()["job_id"])
    assert job["status"] == "done" and job["profile"] is None
    assert not any(e["kind"].startswith("profile.") for e in _events(client, job["job_id"]))
