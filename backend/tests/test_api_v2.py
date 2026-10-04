# backend/tests/test_api_v2.py
"""Live progress, event stream, cancellation, auto-range, input hardening,
crash-tolerant fixing and honest failure — the parts of the API that make it a
service rather than a script. Real git repos, real sandbox subprocesses; only
the model roles are faked (offline stand-in or monkeypatched)."""

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api
import bisector
import diagnoser
import offline
from schema import InMemoryEventStore, InMemoryJobStore, JobState, SqliteEventStore, SqliteJobStore
from tests.test_api import (
    _fake_bisector_call_nemotron, _fake_diagnoser_call_nemotron_other, _make_repo,
)

TERMINAL = ("done", "failed", "cancelled")


def _wait(client, job_id, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/jobs/{job_id}").json()
        if j["status"] in TERMINAL:
            return j
        time.sleep(0.1)
    raise TimeoutError(job_id)


@pytest.fixture
def offline_app(monkeypatch, tmp_path):
    monkeypatch.setenv("CULPRIT_OFFLINE", "1")
    return api.create_app(store=InMemoryJobStore(), event_store=InMemoryEventStore(), data_dir=tmp_path)


@pytest.fixture
def offline_client(offline_app):
    return TestClient(offline_app)


def _kinds(client, job_id):
    return [e["kind"] for e in client.get(f"/jobs/{job_id}/events?limit=2000").json()["events"]]


# ---------------------------------------------------------------------------
# The bundled demo, end to end
# ---------------------------------------------------------------------------

def test_demo_end_to_end_is_resolved_with_a_real_verified_fix(offline_client):
    job_id = offline_client.post("/demo").json()["job_id"]
    job = _wait(offline_client, job_id)

    assert job["status"] == "done" and job["error"] is None and job["mode"] == "offline"
    assert job["final_result"] == "resolved" and job["fix"]["verified"] is True
    # the numbers are measurements: ~10ms baseline, a big regression, recovery
    assert job["fix"]["after_score"] < job["fix"]["before_score"] * 1.15
    reg = next(t for t in job["timeline"] if t["commit"] == job["regression_commit"])
    assert reg["score"] > job["baseline_score"] * 5

    assert len(job["commits"]) == 20 and job["commits"][0]["index"] == 0
    assert job["probes"][0]["role"] == "baseline" and len(job["probes"]) <= 8  # ≪ 19 linear runs
    assert all(len(p["raw_scores"]) >= 3 for p in job["probes"])  # raw runs kept, not just medians
    assert job["window"] is not None and job["threshold_pct"] == 15 and job["n_runs"] == 5
    assert job["diagnosis"]["category"] == "n_plus_one"
    assert job["commits"][next(i for i, c in enumerate(job["commits"]) if c["sha"] == job["regression_commit"])]["subject"].startswith("refactor")

    m = job["metrics"]
    assert m["sandbox"]["runs"] >= 35 and m["sandbox"]["patched_runs"] == 5
    assert m["models"]["nano"]["calls"] >= 4 and m["models"]["ultra"]["calls"] == 2
    assert all(mm["offline"] for mm in m["models"].values())  # labelled as stand-ins everywhere


def test_event_stream_tells_the_whole_story_in_order(offline_client):
    job_id = offline_client.post("/demo").json()["job_id"]
    _wait(offline_client, job_id)
    kinds = _kinds(offline_client, job_id)
    for a, b in [("job.start", "bisect.plan"), ("bisect.plan", "probe.start"), ("probe.start", "sandbox.run"),
                 ("bisect.done", "diagnose.start"), ("citation.check", "diagnose.result"),
                 ("diagnose.result", "fix.start"), ("patch.built", "verify.result"), ("fix.end", "job.done")]:
        assert kinds.index(a) < kinds.index(b), (a, b)
    assert kinds.count("probe.start") == kinds.count("probe.end")  # every span closes
    assert "model.start" in kinds and "model.end" in kinds and "sandbox.deps" in kinds

    evs = offline_client.get(f"/jobs/{job_id}/events?limit=2000").json()["events"]
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    ends = [e for e in evs if e["kind"] == "model.end"]
    assert all("response" in e["data"] and e["data"]["offline"] for e in ends)  # raw responses are inspectable
    starts = [e for e in evs if e["kind"] == "model.start"]
    assert all("request" in e["data"] for e in starts)


def test_event_cursor_paging_and_done_flag(offline_client):
    job_id = offline_client.post("/demo").json()["job_id"]
    _wait(offline_client, job_id)
    seen, cursor, pages = [], 0, 0
    while True:
        page = offline_client.get(f"/jobs/{job_id}/events?after={cursor}&limit=25").json()
        seen += [e["seq"] for e in page["events"]]
        cursor = page["next"]
        pages += 1
        if page["done"]:
            break
        assert pages < 50
    assert seen == list(range(1, len(seen) + 1)) and pages > 1  # no gaps, no repeats
    tail = offline_client.get(f"/jobs/{job_id}/events?after={cursor}").json()
    assert tail["events"] == [] and tail["done"] is True and tail["next"] == cursor
    assert offline_client.get("/jobs/nope/events").status_code == 404


def test_report_patch_history_and_config_endpoints(offline_client):
    job_id = offline_client.post("/demo").json()["job_id"]
    _wait(offline_client, job_id)

    md = offline_client.get(f"/jobs/{job_id}/report.md")
    assert md.status_code == 200 and "text/markdown" in md.headers["content-type"]
    assert "✅ **Verified.**" in md.text and "Offline run" in md.text and "```diff" in md.text

    patch = offline_client.get(f"/jobs/{job_id}/fix.patch")
    assert patch.status_code == 200 and patch.text.startswith("--- a/app/summary.py") and patch.text.endswith("\n")
    assert "attachment" in patch.headers["content-disposition"]

    rows = offline_client.get("/jobs").json()
    assert rows[0]["job_id"] == job_id and rows[0]["final_result"] == "resolved" and rows[0]["category"] == "n_plus_one"
    assert rows[0]["label"].startswith("Bundled demo")

    cfg = offline_client.get("/config").json()
    assert cfg["mode"] == "offline" and cfg["sandbox_backend"] == "local"
    assert offline_client.get("/preflight").json()["mode"] == "offline"
    assert offline_client.get("/health").json() == {"ok": True}
    assert offline_client.get("/jobs/nope/report.md").status_code == 404


# ---------------------------------------------------------------------------
# Live progress + cancellation (need the POST off-thread: TestClient blocks on it)
# ---------------------------------------------------------------------------

def _post_in_thread(app, path, json=None):
    t = threading.Thread(target=lambda: TestClient(app).post(path, json=json), daemon=True)
    t.start()
    return t


def _first_job_id(client, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        rows = client.get("/jobs").json()
        if rows:
            return rows[0]["job_id"]
        time.sleep(0.05)
    raise TimeoutError("job never appeared")


def test_dashboard_data_fills_in_live_not_all_at_the_end(offline_app):
    """The whole point of the event pipeline: mid-run polls must already show
    partial commits/probes/timeline, so the chart and scanner fill in live."""
    client = TestClient(offline_app)
    t = _post_in_thread(offline_app, "/demo")
    job_id = _first_job_id(client)
    partial, statuses = None, set()
    while t.is_alive():
        j = client.get(f"/jobs/{job_id}").json()
        statuses.add(j["status"])
        if j["status"] == "bisecting" and 1 <= len(j["probes"]) < 7 and j["timeline"] and j["commits"]:
            partial = partial or j
        time.sleep(0.03)
    t.join()
    assert partial is not None, f"never observed partial progress; statuses seen: {statuses}"
    assert partial["baseline_score"] is not None
    # (Later stages can last milliseconds with the instant offline stand-in, so
    # whether a poller happens to catch "diagnosing" is timing, not behavior.)
    assert "bisecting" in statuses


def test_cancel_stops_a_running_job_cleanly(monkeypatch, tmp_path):
    monkeypatch.setattr(bisector, "call_nemotron", _fake_bisector_call_nemotron)
    monkeypatch.setattr(api, "_DEFAULT_N_RUNS", 2)
    slow = "import time\ntime.sleep(0.25)\nprint(10.0)\n"
    repo, shas = _make_repo(tmp_path, [slow] * 8)
    app = api.create_app(store=InMemoryJobStore(), event_store=InMemoryEventStore(), data_dir=tmp_path / "d")
    client = TestClient(app)
    t = _post_in_thread(app, "/analyze", {"repo_url": str(repo), "benchmark_command": "python bench.py",
                                          "commit_range": [shas[0], shas[-1]]})
    job_id = _first_job_id(client)
    end = time.time() + 30
    while "probe.start" not in _kinds(client, job_id):
        assert time.time() < end
        time.sleep(0.05)

    resp = client.post(f"/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    t.join(timeout=40)
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "cancelled" and job["cancel_requested"] is True and job["error"] is None
    kinds = _kinds(client, job_id)
    assert "job.cancel_requested" in kinds and kinds[-1] == "job.cancelled"
    assert client.post(f"/jobs/{job_id}/cancel").status_code == 409  # already terminal
    assert client.post("/jobs/nope/cancel").status_code == 404


# ---------------------------------------------------------------------------
# Range resolution + input hardening
# ---------------------------------------------------------------------------

@pytest.fixture
def plain_client(monkeypatch, tmp_path):
    monkeypatch.setattr(bisector, "call_nemotron", _fake_bisector_call_nemotron)
    monkeypatch.setattr(diagnoser, "call_nemotron", _fake_diagnoser_call_nemotron_other)
    return TestClient(api.create_app(store=InMemoryJobStore(), event_store=InMemoryEventStore(), data_dir=tmp_path / "d"))


FAST = "import time\nstart=time.perf_counter()\ntotal=0\nfor i in range(300_000): total+=i\nprint((time.perf_counter()-start)*1000)\n"
SLOW = "import time\nstart=time.perf_counter()\ntotal=0\nfor _ in range(8):\n    for i in range(300_000): total+=i\nprint((time.perf_counter()-start)*1000)\n"


def test_omitted_range_is_auto_detected_and_recorded_as_full_shas(plain_client, tmp_path):
    repo, shas = _make_repo(tmp_path, [FAST, FAST, SLOW, SLOW, SLOW])
    job_id = plain_client.post("/analyze", json={"repo_url": str(repo), "benchmark_command": "python bench.py"}).json()["job_id"]
    job = _wait(plain_client, job_id)
    assert job["status"] == "done" and job["error"] is None
    assert job["commit_range"] == [shas[0], shas[-1]]  # clamped to the repo's whole (short) history
    assert job["regression_commit"] == shas[2]
    evs = plain_client.get(f"/jobs/{job_id}/events").json()["events"]
    assert next(e for e in evs if e["kind"] == "git.range")["data"]["auto"] is True


def test_branch_and_relative_revs_are_accepted(plain_client, tmp_path):
    repo, shas = _make_repo(tmp_path, [FAST, FAST, SLOW, SLOW, SLOW])
    job_id = plain_client.post("/analyze", json={
        "repo_url": str(repo), "benchmark_command": "python bench.py", "commit_range": ["HEAD~4", "HEAD"],
    }).json()["job_id"]
    job = _wait(plain_client, job_id)
    assert job["status"] == "done" and job["commit_range"] == [shas[0], shas[-1]]


def test_end_of_range_not_regressed_is_answered_in_one_probe(plain_client, tmp_path):
    repo, shas = _make_repo(tmp_path, [FAST] * 6)
    job_id = plain_client.post("/analyze", json={
        "repo_url": str(repo), "benchmark_command": "python bench.py", "commit_range": [shas[0], shas[-1]],
    }).json()["job_id"]
    job = _wait(plain_client, job_id)
    assert job["status"] == "done" and job["regression_commit"] is None and job["diagnosis"] is None
    assert len(job["probes"]) == 2  # baseline + the confirming end-of-range probe, not ~log2(n) more


@pytest.mark.parametrize("rng,fragment", [
    (["0" * 40, "HEAD"], "not a commit"),
    (["HEAD", "HEAD"], "same commit"),
])
def test_bad_ranges_fail_with_a_clear_error(plain_client, tmp_path, rng, fragment):
    repo, shas = _make_repo(tmp_path, [FAST, FAST, SLOW])
    job_id = plain_client.post("/analyze", json={"repo_url": str(repo), "benchmark_command": "python bench.py", "commit_range": rng}).json()["job_id"]
    job = _wait(plain_client, job_id)
    assert job["status"] == "failed" and fragment in job["error"]


def test_reversed_range_is_caught_before_any_benchmark_runs(plain_client, tmp_path):
    repo, shas = _make_repo(tmp_path, [FAST, FAST, SLOW])
    job_id = plain_client.post("/analyze", json={
        "repo_url": str(repo), "benchmark_command": "python bench.py", "commit_range": [shas[-1], shas[0]],
    }).json()["job_id"]
    job = _wait(plain_client, job_id)
    assert job["status"] == "failed" and "not an ancestor" in job["error"]
    assert "sandbox.run" not in _kinds(plain_client, job_id)  # failed fast, spent nothing


def test_git_option_injection_is_rejected_at_the_door(plain_client):
    assert plain_client.post("/analyze", json={"repo_url": "--upload-pack=touch /tmp/x", "benchmark_command": "b"}).status_code == 422
    r = plain_client.post("/analyze", json={"repo_url": "/tmp/r", "benchmark_command": "b", "commit_range": ["--output=/tmp/x", "HEAD"]})
    assert r.status_code == 400
    assert plain_client.post("/analyze", json={"repo_url": "   ", "benchmark_command": "b"}).status_code == 422


@pytest.mark.parametrize("url", ["ext::sh -c touch% /tmp/pwned", "file:///etc", "ftp://example.com/r.git", "/etc/passwd"])
def test_dangerous_or_unsupported_repo_urls_never_reach_git(plain_client, url):
    job_id = plain_client.post("/analyze", json={"repo_url": url, "benchmark_command": "python bench.py"}).json()["job_id"]
    job = _wait(plain_client, job_id)
    assert job["status"] == "failed" and "supported remote URL" in job["error"]
    assert not Path("/tmp/pwned").exists()


def test_local_paths_are_refused_when_the_token_factory_backend_is_active(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_USE_TOKEN_FACTORY", True)
    monkeypatch.delenv("CULPRIT_OFFLINE", raising=False)
    monkeypatch.delenv("CULPRIT_ALLOW_LOCAL_REPOS", raising=False)
    repo, shas = _make_repo(tmp_path, [FAST, SLOW])
    client = TestClient(api.create_app(store=InMemoryJobStore(), event_store=InMemoryEventStore(), data_dir=tmp_path / "d"))
    job_id = client.post("/analyze", json={"repo_url": str(repo), "benchmark_command": "python bench.py"}).json()["job_id"]
    job = _wait(client, job_id)
    assert job["status"] == "failed" and "local repository paths are disabled" in job["error"]


# ---------------------------------------------------------------------------
# Fixer honesty: crashes are failed attempts (with feedback), never dead jobs,
# and an ineffective fix is reported as unresolved, never dressed up.
# ---------------------------------------------------------------------------

def test_a_patch_that_crashes_is_a_failed_attempt_with_feedback_not_a_failed_job(monkeypatch, offline_client):
    real_fixer, seen = offline._fixer, []

    def flaky_fixer(payload):
        seen.append(payload["previous_attempt_result"])
        if len(seen) == 1:  # the model's first patch breaks the code
            return {"edits": [{"file": "app/summary.py", "old": "    return [serialize_order(o) for o in orders]",
                               "new": '    raise RuntimeError("boom")'}],
                    "rationale": "first try", "confidence_will_resolve": "low"}
        return real_fixer(payload)

    monkeypatch.setattr(offline, "_fixer", flaky_fixer)
    job = _wait(offline_client, offline_client.post("/demo").json()["job_id"])

    assert job["status"] == "done" and job["error"] is None  # the crash did NOT kill the job
    a1, a2 = job["fix_attempts"]
    assert a1["resolved"] is False and "boom" in a1["error"]  # honest: no fake score for a run that never scored
    assert a2["resolved"] is True and a2["error"] is None
    assert job["final_result"] == "resolved" and job["fix"]["verified"] is True
    assert seen[1]["note"] and "boom" in seen[1]["note"]  # the traceback was fed back to the model
    assert "verify.crashed" in _kinds(offline_client, job["job_id"])


def test_a_fix_that_never_works_is_reported_as_unresolved_after_three_real_attempts(monkeypatch, offline_client):
    def useless_fixer(payload):
        return {"edits": [{"file": "app/summary.py", "old": "def get_order_summary(db, customer_id):",
                           "new": "def get_order_summary(db, customer_id):\n    # tried something"}],
                "rationale": "cosmetic", "confidence_will_resolve": "low"}

    monkeypatch.setattr(offline, "_fixer", useless_fixer)
    job = _wait(offline_client, offline_client.post("/demo").json()["job_id"])

    assert job["status"] == "done" and job["final_result"] == "unresolved_diagnosis_only"
    assert len(job["fix_attempts"]) == 3 and not any(a["resolved"] for a in job["fix_attempts"])
    assert job["fix"]["verified"] is False  # never claims success
    assert all(a["score_after"] > job["baseline_score"] * 5 for a in job["fix_attempts"])  # real, still-slow measurements
    md = offline_client.get(f"/jobs/{job['job_id']}/report.md").text
    assert "✅" not in md and "do not merge it blindly" in md


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------

def test_jobs_orphaned_by_a_restart_are_marked_failed_on_boot(tmp_path):
    db = tmp_path / "c.db"
    store = SqliteJobStore(db)
    store.create(JobState(job_id="zombie", status="diagnosing", repo_url="/r", benchmark_command="b",
                          created_at="2026-09-01T00:00:00Z", updated_at="2026-09-01T00:00:00Z"))
    client = TestClient(api.create_app(store=SqliteJobStore(db), event_store=SqliteEventStore(SqliteJobStore(db)), data_dir=tmp_path))
    j = client.get("/jobs/zombie").json()
    assert j["status"] == "failed" and "restarted" in j["error"]


def test_sqlite_backed_app_persists_a_full_run(monkeypatch, tmp_path):
    monkeypatch.setenv("CULPRIT_OFFLINE", "1")
    db = tmp_path / "c.db"

    def boot():
        s = SqliteJobStore(db)
        return TestClient(api.create_app(store=s, event_store=SqliteEventStore(s), data_dir=tmp_path))

    c1 = boot()
    job_id = c1.post("/demo").json()["job_id"]
    first = _wait(c1, job_id)
    n_events = len(c1.get(f"/jobs/{job_id}/events?limit=2000").json()["events"])

    c2 = boot()  # a brand-new process, same database
    again = c2.get(f"/jobs/{job_id}").json()
    assert again["final_result"] == first["final_result"] == "resolved"
    assert len(c2.get(f"/jobs/{job_id}/events?limit=2000").json()["events"]) == n_events
    assert c2.get("/jobs").json()[0]["job_id"] == job_id
