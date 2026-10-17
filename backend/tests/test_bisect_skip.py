# backend/tests/test_bisect_skip.py
"""`git bisect skip` for Culprit: a midpoint that can't be built or run is stepped
over instead of killing the search; when skips make the answer genuinely
ambiguous the job says so (with the suspects) instead of guessing. Layer A is
checked exhaustively on synthetic histories; Layer B and the API use real git
repos and real sandbox subprocesses (only the models are faked)."""

import itertools
import json
import random
import subprocess

import pytest
from fastapi.testclient import TestClient

import bisector
import diagnoser
from api import InMemoryJobStore, create_app
from bisector import (
    BisectionAmbiguous, BisectionError, CandidateEvaluation, CommitUnrunnable, bisect,
)
from models import NemotronResult
from sandbox_client import LocalGitSandbox
from tests.test_api import (
    _fake_bisector_call_nemotron, _fake_diagnoser_call_nemotron_other, _make_repo, _wait_for_terminal,
)


def _ev(verdict):
    return CandidateEvaluation(verdict=verdict, median_score=100.0, pct_change_from_baseline=0.0,
                               raw_scores=[100.0], rounds=1)


def _run(n, k, unrunnable, **kw):
    """Search a synthetic history whose true first-bad commit is index k
    (k == n: no regression) and whose commits in `unrunnable` can't be run."""
    commits = [f"c{i:03d}" for i in range(n)]
    calls = []

    def evaluate(sha):
        i = commits.index(sha)
        calls.append(i)
        if i in unrunnable:
            raise CommitUnrunnable(sha, "boom")
        return _ev("regressed" if i >= k else "clean")

    skipped = []
    out = bisect(commits, evaluate=evaluate, skipped_out=skipped, **kw)
    return commits, out, calls, skipped


# ---------------------------------------------------------------------------
# Layer A
# ---------------------------------------------------------------------------

def test_a_skipped_midpoint_is_stepped_over_and_the_answer_is_still_exact():
    commits, (result, visited), calls, skipped = _run(9, 6, {4})  # first probe (index 4) is unrunnable
    assert result == commits[6]
    assert [i for i, _ in skipped] == [4] and skipped[0][1].reason == "boom"
    assert 4 in calls and len(set(calls)) == len(calls)  # each commit tried at most once


def test_nearest_runnable_neighbour_is_probed_after_a_skip():
    seen = []
    commits = [f"c{i}" for i in range(7)]

    def evaluate(sha):
        seen.append(sha)
        if sha == "c3":
            raise CommitUnrunnable(sha, "x")
        return _ev("regressed" if int(sha[1:]) >= 5 else "clean")

    bisect(commits, evaluate=evaluate)
    assert seen[0] == "c3" and seen[1] == "c4"  # mid, then the neighbour above it


def test_on_step_reports_skips_without_a_narrowed_event():
    events = []
    commits = [f"c{i}" for i in range(7)]

    def evaluate(sha):
        if sha == "c3":
            raise CommitUnrunnable(sha)
        return _ev("regressed" if sha >= "c5" else "clean")

    result, _ = bisect(commits, evaluate=evaluate, on_step=lambda e, lo, hi, mid, ev: events.append((e, mid)))
    assert result == "c5"
    assert ("skipped", 3) in events and ("narrowed", 3) not in events


def test_when_every_suspect_is_unrunnable_it_says_so_instead_of_guessing():
    # c3 is the true first-bad commit but can't run; c2 is clean, c4 is regressed.
    with pytest.raises(BisectionAmbiguous) as e:
        _run(7, 3, {3})
    assert e.value.suspects == ["c003"] and e.value.boundary == "c004"
    assert "c003" in str(e.value) and "c004" in str(e.value) and "could not be run" in str(e.value)
    assert isinstance(e.value, BisectionError)  # existing handlers still catch it


def test_too_many_skips_gives_up_with_a_clear_error():
    with pytest.raises(BisectionError) as e:
        _run(30, 29, set(range(0, 29)), max_skips=3)
    assert "unrunnable" in str(e.value)


def test_no_skips_means_exactly_the_old_search():
    commits, (result, visited), calls, skipped = _run(16, 5, set())
    assert result == commits[5] and skipped == [] and len(visited) <= 5


@pytest.mark.parametrize("seed", range(40))
def test_skips_never_cause_a_wrong_answer(seed):
    """Whatever subset is unrunnable, the search either names the true first-bad
    commit, or admits ambiguity with suspects that are all genuinely unrunnable
    and that include (or sit right before) the truth. It never names a wrong commit."""
    rng = random.Random(seed)
    n = rng.randint(1, 16)
    k = rng.randint(0, n)  # k == n ⇒ no regression anywhere
    unrunnable = {i for i in range(n) if rng.random() < 0.35}
    commits = [f"c{i:03d}" for i in range(n)]
    try:
        _, (result, _), calls, _ = _run(n, k, unrunnable)
    except BisectionAmbiguous as amb:
        idx = {c: i for i, c in enumerate(commits)}
        assert amb.suspects and all(idx[s] in unrunnable for s in amb.suspects)
        truth = commits[k] if k < n else None
        assert truth is None or truth in amb.suspects or truth == amb.boundary
        return
    assert result == (commits[k] if k < n else None)
    assert not (set(calls) - set(range(n)))


def test_exhaustive_small_histories():
    for n in range(1, 7):
        for k in range(0, n + 1):
            for mask in itertools.product([False, True], repeat=n):
                unrunnable = {i for i, m in enumerate(mask) if m}
                commits = [f"c{i:03d}" for i in range(n)]
                try:
                    _, (result, _), _, _ = _run(n, k, unrunnable)
                except BisectionAmbiguous as amb:
                    truth = commits[k] if k < n else None
                    assert truth is None or truth in amb.suspects or truth == amb.boundary
                    continue
                assert result == (commits[k] if k < n else None), (n, k, unrunnable)


# ---------------------------------------------------------------------------
# Layer B — real git + real sandbox, fake Nano
# ---------------------------------------------------------------------------

# Sleep-based benchmarks: wall-clock steady enough that the 15% threshold is never
# ambiguous even on a loaded CI box (CPU-bound loops made these tests flaky).
FAST = "import time\nstart=time.perf_counter()\ntime.sleep(0.05)\nprint((time.perf_counter()-start)*1000)\n"
SLOW = "import time\nstart=time.perf_counter()\ntime.sleep(0.5)\nprint((time.perf_counter()-start)*1000)\n"
BROKEN = "raise SystemExit('this commit does not build')\n"


def _fake_nano(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
    return _fake_bisector_call_nemotron(job_id=job_id, step=step, model=model, system_prompt=system_prompt,
                                        payload=payload, temperature=temperature)


def test_bisect_repo_steps_over_a_broken_commit_and_records_it(tmp_path, monkeypatch):
    monkeypatch.setattr(bisector, "call_nemotron", _fake_nano)
    # c0 fast | c1 fast | c2 SLOW (guilty) | c3 BROKEN | c4..c6 slow
    repo, shas = _make_repo(tmp_path, [FAST, FAST, SLOW, BROKEN, SLOW, SLOW, SLOW])
    events = []

    class Rep(bisector.Reporter):
        def __init__(self):
            super().__init__("j", lambda ev: events.append(ev), None)

    skipped_cb = []
    result = bisector.bisect_repo(
        job_id="j", repo_path=str(repo), start_sha=shas[0], end_sha=shas[-1], benchmark_command="python bench.py",
        sandbox=LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache")), n_runs=3,
        reporter=Rep(), on_skip=skipped_cb.append,
    )
    assert result.regression_commit == shas[2]
    assert [s.sha for s in result.skipped] == [shas[3]] and result.skipped[0].index == 3
    assert "this commit does not build" in result.skipped[0].reason
    assert skipped_cb == result.skipped
    assert shas[3] not in {p.commit for p in result.probes}  # never scored
    kinds = [e.kind for e in events]
    assert "bisect.skip" in kinds
    ended = [e for e in events if e.kind == "probe.end" and e.data.get("verdict") == "skipped"]
    assert len(ended) == 1 and ended[0].data["skipped"] is True


def test_a_broken_baseline_still_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(bisector, "call_nemotron", _fake_nano)
    repo, shas = _make_repo(tmp_path, [BROKEN, FAST, SLOW])
    with pytest.raises(Exception) as e:
        bisector.bisect_repo(job_id="j", repo_path=str(repo), start_sha=shas[0], end_sha=shas[-1],
                             benchmark_command="python bench.py",
                             sandbox=LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache")), n_runs=3)
    assert not isinstance(e.value, (BisectionAmbiguous, CommitUnrunnable))


def test_a_broken_end_of_range_still_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(bisector, "call_nemotron", _fake_nano)
    repo, shas = _make_repo(tmp_path, [FAST, FAST, BROKEN])
    with pytest.raises(Exception) as e:
        bisector.bisect_repo(job_id="j", repo_path=str(repo), start_sha=shas[0], end_sha=shas[-1],
                             benchmark_command="python bench.py",
                             sandbox=LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache")), n_runs=3)
    assert not isinstance(e.value, (BisectionAmbiguous, CommitUnrunnable))


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bisector, "call_nemotron", _fake_nano)
    monkeypatch.setattr(diagnoser, "call_nemotron", _fake_diagnoser_call_nemotron_other)
    return TestClient(create_app(store=InMemoryJobStore()))


def _analyze(client, repo, shas):
    r = client.post("/analyze", json={"repo_url": str(repo), "benchmark_command": "python bench.py",
                                      "commit_range": [shas[0], shas[-1]]})
    return _wait_for_terminal(client, r.json()["job_id"], timeout=90)


def test_job_finds_the_guilty_commit_past_a_broken_one_and_reports_the_skip(tmp_path, client):
    repo, shas = _make_repo(tmp_path, [FAST, FAST, SLOW, BROKEN, SLOW, SLOW, SLOW])
    job = _analyze(client, repo, shas)
    assert job["status"] == "done", job.get("error")
    assert job["regression_commit"] == shas[2]
    assert [s["sha"] for s in job["skipped"]] == [shas[3]]
    assert "does not build" in job["skipped"][0]["reason"]
    md = client.get(f"/jobs/{job['job_id']}/report.md").text
    assert "could not be built or run and were skipped" in md and shas[3][:8] in md


def test_job_with_an_unresolvable_suspect_fails_honestly_and_names_it(tmp_path, client):
    # the broken commit sits exactly between the last clean and the first regressed one
    repo, shas = _make_repo(tmp_path, [FAST, FAST, BROKEN, SLOW, SLOW, SLOW, SLOW])
    job = _analyze(client, repo, shas)
    assert job["status"] == "failed"
    assert "BisectionAmbiguous" in job["error"] and shas[2][:8] in job["error"] and shas[3][:8] in job["error"]
    assert [s["sha"] for s in job["skipped"]] == [shas[2]]  # persisted even though the job failed
    assert job["regression_commit"] is None  # never guessed


def test_a_job_with_no_broken_commits_has_no_skips(tmp_path, client):
    repo, shas = _make_repo(tmp_path, [FAST, FAST, SLOW, SLOW, SLOW])
    job = _analyze(client, repo, shas)
    assert job["status"] == "done" and job["skipped"] == [] and job["regression_commit"] == shas[2]
