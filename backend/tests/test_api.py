# backend/tests/test_api.py
"""
Exercises the real HTTP layer (FastAPI TestClient) end-to-end: real git
repos, real LocalGitSandbox subprocess benchmark runs, Nano faked via
monkeypatch (no live NEBIUS_API_KEY in this environment) using the same
reference decision as test_bisector.py. This is what BUILD_01's "Hand back"
section asks for: "one real end-to-end run's job JSON (repo in -> guilty
commit + timeline out) with no model errors swallowed."
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bisector  # noqa: E402  (module import so monkeypatch.setattr works)
from api import InMemoryJobStore, create_app  # noqa: E402
from models import NemotronResult  # noqa: E402
from tests.test_bisector import _reference_decision  # noqa: E402


def _fake_call_nemotron(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
    d = _reference_decision(payload["candidate_scores"], payload["baseline_score"], payload["regression_threshold_pct"])
    return NemotronResult(parsed=d, raw_response=json.dumps(d), attempts=1, model_id="fake-nano", latency_s=0.0)


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


def _wait_for_terminal(client: TestClient, job_id: str, timeout: float = 20.0) -> dict:
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
    monkeypatch.setattr(bisector, "call_nemotron", _fake_call_nemotron)
    app = create_app(store=InMemoryJobStore())
    return TestClient(app)


def test_missing_commit_range_is_400(client):
    resp = client.post("/analyze", json={"repo_url": "/tmp/whatever", "benchmark_command": "python bench.py"})
    assert resp.status_code == 400


def test_unknown_job_returns_404(client):
    resp = client.get("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_end_to_end_real_regression_found(tmp_path, client):
    """The core BUILD_01 deliverable: repo in -> guilty commit + timeline out."""
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
    # Stack 2 doesn't exist yet — these must be honestly empty, not fabricated.
    assert job["diagnosis"] is None
    assert job["fix"] is None
    assert job["final_result"] is None


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
    assert job["final_result"] is None  # neither "resolved" nor "unresolved_..." applies — nothing to diagnose


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
