# backend/tests/test_api.py
"""
Exercises the real HTTP layer (FastAPI TestClient) end-to-end: real git
repos, real LocalGitSandbox subprocess benchmark runs, Nemotron faked via
monkeypatch (no live NEBIUS_API_KEY in this environment) using the same
reference-decision pattern as test_bisector.py — but now across the WHOLE
pipeline (bisect -> diagnose -> fix), since api.py wires diagnoser.py/
fixer.py in for real (CODE_REVIEW_FINDINGS.md #3).
"""

import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import bisector  # noqa: E402  (module import so monkeypatch.setattr works)
import diagnoser  # noqa: E402
import fixer  # noqa: E402
from api import InMemoryJobStore, create_app  # noqa: E402
from models import NemotronResult  # noqa: E402
from tests.test_bisector import _reference_decision  # noqa: E402


def _fake_bisector_call_nemotron(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
    d = _reference_decision(payload["candidate_scores"], payload["baseline_score"], payload["regression_threshold_pct"])
    return NemotronResult(parsed=d, raw_response=json.dumps(d), attempts=1, model_id="fake-nano", latency_s=0.0)


def _fake_diagnoser_call_nemotron_other(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
    """category="other" needs no citations at all — the simplest possible
    fake that still exercises the real diagnose() schema-validation path,
    for tests that don't care about the Fixer stage."""
    d = {
        "category": "other",
        "explanation": "No single line change clearly explains the regression from the diff alone.",
        "cited_lines": [],
        "confidence": "low",
    }
    return NemotronResult(parsed=d, raw_response=json.dumps(d), attempts=1, model_id="fake-ultra", latency_s=0.0)


# The nested-loop diff this fakes a diagnosis/fix for. Guilty commit content
# is "# commit 2\n" + this "slow" body; cited_lines below must be an exact
# `+` line in the real `git diff` api.py computes between commit 1 and 2 —
# _verify_cited_lines() (CODE_REVIEW_FINDINGS.md #6) is NOT mocked, so this
# has to genuinely verify.
_SLOW_BENCH = (
    "import time\nstart=time.perf_counter()\ntotal=0\n"
    "for _ in range(6):\n    for i in range(500_000): total+=i\n"
    "print((time.perf_counter()-start)*1000)\n"
)

# Applied on top of the guilty commit's checkout (whose bench.py is exactly
# "# commit 2\n" + _SLOW_BENCH, 7 lines) by the REAL sandbox via a REAL
# `git apply` + REAL benchmark re-run — nothing about verification is
# mocked, only the two Nemotron calls that produce this text are.
_FIX_PATCH = (
    "--- a/bench.py\n+++ b/bench.py\n@@ -1,7 +1,6 @@\n"
    " # commit 2\n import time\n start=time.perf_counter()\n total=0\n"
    "-for _ in range(6):\n-    for i in range(500_000): total+=i\n"
    "+for i in range(500_000): total+=i\n"
    " print((time.perf_counter()-start)*1000)\n"
)


def _fake_diagnoser_call_nemotron_real(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
    d = {
        "category": "algorithmic_complexity",
        "explanation": "An extra outer loop was added, multiplying the work by 6x for no reason.",
        "cited_lines": ["for _ in range(6):"],
        "confidence": "high",
    }
    return NemotronResult(parsed=d, raw_response=json.dumps(d), attempts=1, model_id="fake-ultra", latency_s=0.0)


def _fake_fixer_call_nemotron(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
    d = {
        "patch": _FIX_PATCH,
        "rationale": "Removed the redundant outer loop, restoring the original O(n) behavior.",
        "confidence_will_resolve": "high",
    }
    return NemotronResult(parsed=d, raw_response=json.dumps(d), attempts=1, model_id="fake-ultra", latency_s=0.0)


def _make_repo(tmp_path, bench_contents: list[str]) -> tuple[Path, list[str]]:
    repo = tmp_path / "repo"
    repo.mkdir()

    def run(*a):
        subprocess.run(a, cwd=repo, check=True, capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@e.com")
    run("git", "config", "user.name", "T")

    shas = []
    for idx, content in enumerate(bench_contents):
        (repo / "bench.py").write_text(f"# commit {idx}\n{content}")
        run("git", "add", ".")
        run("git", "commit", "-q", "-m", f"commit {idx}")
        shas.append(subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip())
    return repo, shas


def _wait_for_terminal(client: TestClient, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in ("done", "failed"):
            return last
        time.sleep(0.1)
    raise TimeoutError(f"job {job_id} never reached a terminal status; last seen: {last}")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bisector, "call_nemotron", _fake_bisector_call_nemotron)
    app = create_app(store=InMemoryJobStore())
    return TestClient(app)


def test_missing_commit_range_is_accepted_and_auto_detected(client):
    """commit_range used to be a hard 400 (api.py flag #3). It is now optional —
    the range is auto-detected inside the job, so problems (here: not a repo)
    surface as a clear *failed job*, not a request error."""
    resp = client.post("/analyze", json={"repo_url": "/tmp/definitely-not-a-repo", "benchmark_command": "python bench.py"})
    assert resp.status_code == 200
    job = _wait_for_terminal(client, resp.json()["job_id"])
    assert job["status"] == "failed"
    assert "not a git repository" in job["error"]


def test_malformed_commit_range_is_400(client):
    resp = client.post("/analyze", json={
        "repo_url": "/tmp/whatever", "benchmark_command": "python bench.py", "commit_range": ["only-one"],
    })
    assert resp.status_code == 400


def test_unknown_job_returns_404(client):
    resp = client.get("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_cors_middleware_allows_configured_origin(client):
    """CODE_REVIEW_FINDINGS.md #1 — without CORSMiddleware, a browser
    blocks every request from the dashboard's origin outright; FastAPI's
    TestClient doesn't enforce CORS itself, so this has to check the
    response header a browser would actually check, not just that the
    request "worked" (it always would)."""
    resp = client.get("/jobs/does-not-exist", headers={"Origin": "http://localhost:3000"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_end_to_end_real_regression_found_diagnosed_as_other(tmp_path, client, monkeypatch):
    """repo in -> guilty commit + timeline -> diagnosis out. category="other"
    means the Fixer never runs — an honest "diagnosed, nothing concrete to
    patch" result, not a fabricated fix (module docstring flag #1)."""
    monkeypatch.setattr(diagnoser, "call_nemotron", _fake_diagnoser_call_nemotron_other)

    fast = "import time\nstart=time.perf_counter()\ntotal=0\nfor i in range(500_000): total+=i\nprint((time.perf_counter()-start)*1000)\n"
    slow = "import time\nstart=time.perf_counter()\ntotal=0\nfor _ in range(6):\n    for i in range(500_000): total+=i\nprint((time.perf_counter()-start)*1000)\n"
    repo, shas = _make_repo(tmp_path, [fast, fast, slow, slow, slow])

    resp = client.post("/analyze", json={
        "repo_url": str(repo), "benchmark_command": "python bench.py",
        "commit_range": [shas[0], shas[-1]],
    })
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    job = _wait_for_terminal(client, job_id)
    assert job["status"] == "done"
    assert job["error"] is None
    assert job["regression_commit"] == shas[2]
    assert job["timeline"][0]["commit"] == shas[0]
    assert len(job["timeline"]) >= 2
    assert job["diagnosis"] is not None
    assert job["diagnosis"]["category"] == "other"
    # "other" has nothing concrete to fix — must stay honestly empty, not fabricated.
    assert job["fix"] is None
    assert job["fix_attempts"] == []
    assert job["final_result"] is None


def test_end_to_end_regression_diagnosed_and_fixed(tmp_path, client, monkeypatch):
    """The full pipeline, nothing mocked except the two Nemotron calls:
    real bisection, real diagnoser citation verification against a real
    diff, real `git apply` + real benchmark re-run to verify the fix."""
    monkeypatch.setattr(diagnoser, "call_nemotron", _fake_diagnoser_call_nemotron_real)
    monkeypatch.setattr(fixer, "call_nemotron", _fake_fixer_call_nemotron)

    fast = "import time\nstart=time.perf_counter()\ntotal=0\nfor i in range(500_000): total+=i\nprint((time.perf_counter()-start)*1000)\n"
    repo, shas = _make_repo(tmp_path, [fast, fast, _SLOW_BENCH, _SLOW_BENCH, _SLOW_BENCH])

    resp = client.post("/analyze", json={
        "repo_url": str(repo), "benchmark_command": "python bench.py",
        "commit_range": [shas[0], shas[-1]],
    })
    job_id = resp.json()["job_id"]

    job = _wait_for_terminal(client, job_id)
    assert job["status"] == "done"
    assert job["error"] is None
    assert job["regression_commit"] == shas[2]
    assert job["diagnosis"]["category"] == "algorithmic_complexity"
    assert job["diagnosis"]["cited_lines"] == ["for _ in range(6):"]
    assert job["diagnosis"]["citation_verification_failed"] is False
    assert job["diagnosis"]["diff"] is not None and "for _ in range(6):" in job["diagnosis"]["diff"]
    assert len(job["fix_attempts"]) == 1
    assert job["fix_attempts"][0]["resolved"] is True
    assert job["fix"] is not None
    assert job["fix"]["verified"] is True
    assert job["fix"]["patch_diff"] == _FIX_PATCH
    assert job["final_result"] == "resolved"


def test_end_to_end_no_regression_in_range(tmp_path, client):
    fast = "import time\nstart=time.perf_counter()\ntotal=0\nfor i in range(500_000): total+=i\nprint((time.perf_counter()-start)*1000)\n"
    repo, shas = _make_repo(tmp_path, [fast, fast, fast, fast])

    resp = client.post("/analyze", json={
        "repo_url": str(repo), "benchmark_command": "python bench.py",
        "commit_range": [shas[0], shas[-1]],
    })
    job_id = resp.json()["job_id"]

    job = _wait_for_terminal(client, job_id)
    assert job["status"] == "done"
    assert job["regression_commit"] is None
    assert job["diagnosis"] is None  # nothing to diagnose
    assert job["final_result"] is None  # neither "resolved" nor "unresolved_..." applies


def test_broken_benchmark_surfaces_as_failed_with_clear_error(tmp_path, client):
    """AGENTS.md #1: a sandbox failure must fail the job with a clear error,
    never a fabricated result. Confirms it actually reaches the client, not
    just gets logged and silently dropped."""
    repo, shas = _make_repo(tmp_path, ["print('not a number')", "print('still not a number')"])

    resp = client.post("/analyze", json={
        "repo_url": str(repo), "benchmark_command": "python bench.py",
        "commit_range": [shas[0], shas[-1]],
    })
    job_id = resp.json()["job_id"]

    job = _wait_for_terminal(client, job_id)
    assert job["status"] == "failed"
    assert job["error"] is not None
    assert "SandboxError" in job["error"] or "numeric score" in job["error"]


def test_resolve_repo_path_wraps_clone_timeout(tmp_path, monkeypatch):
    """CODE_REVIEW_FINDINGS.md #7's second spot: a hanging `git clone` must
    surface as a clean ValueError (caught by _run_analysis and turned into
    a failed job with a real error message), not an uncaught
    subprocess.TimeoutExpired."""
    import api

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 300))

    monkeypatch.setattr(api.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="timed out"):
        api._resolve_repo_path("https://example.com/not-a-real-repo.git", tmp_path)
