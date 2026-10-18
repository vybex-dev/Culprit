# backend/tests/test_bisect_parallel.py
"""Parallel (k-ary) probing: the same answer as the sequential search, found in
fewer sequential rounds. Layer A is checked exhaustively / by property on
synthetic histories; Layer B and the API use real git repos and real sandbox
subprocesses (only the models are faked)."""

import itertools
import math
import random
import threading
import time

import pytest
from fastapi.testclient import TestClient

import api
import bisector
import diagnoser
from api import InMemoryJobStore, create_app
from bisector import (
    BisectionAmbiguous, BisectionError, CandidateEvaluation, CommitUnrunnable,
    _spread_picks, bisect, bisect_parallel,
)
from sandbox_client import LocalGitSandbox
from tests.test_api import _make_repo, _wait_for_terminal
from tests.test_bisect_skip import (
    BROKEN, FAST, SLOW, _ev, _fake_nano, _fake_diagnoser_call_nemotron_other,
)


def _run_p(n, k, p, unrunnable=frozenset(), **kw):
    commits = [f"c{i:03d}" for i in range(n)]
    calls, rounds = [], []

    def evaluate(sha):
        i = commits.index(sha)
        calls.append(i)
        if i in unrunnable:
            raise CommitUnrunnable(sha, "boom")
        return _ev("regressed" if i >= k else "clean")

    skipped = []
    out = bisect_parallel(commits, evaluate=evaluate, parallelism=p, skipped_out=skipped,
                          on_round=lambda ev, lo, hi, picks, res: rounds.append((ev, lo, hi, list(picks))), **kw)
    return commits, out, calls, skipped, rounds


# ---------------------------------------------------------------------------
# Layer A
# ---------------------------------------------------------------------------

def test_spread_picks_cut_the_window_evenly_and_stay_in_range():
    assert _spread_picks(0, 11, 3, set()) == [2, 5, 8]
    assert _spread_picks(0, 2, 3, set()) == [0, 1, 2]  # window <= P: probe all of it
    assert _spread_picks(0, 0, 4, set()) == [0]
    for lo, hi, p in itertools.product(range(0, 5), range(0, 30), range(2, 7)):
        if hi < lo:
            continue
        picks = _spread_picks(lo, hi, p, set())
        assert picks == sorted(set(picks)) and all(lo <= i <= hi for i in picks) and 1 <= len(picks) <= p


def test_spread_picks_replace_a_skipped_target_with_a_neighbour_and_never_repeat():
    picks = _spread_picks(0, 11, 3, {5})
    assert 5 not in picks and len(picks) == 3 and len(set(picks)) == 3
    assert _spread_picks(0, 3, 3, {0, 1, 2, 3}) == []


def test_needs_at_least_two_probes_at_a_time():
    with pytest.raises(ValueError):
        bisect_parallel(["a", "b"], evaluate=lambda s: _ev("clean"), parallelism=1)


def test_finds_the_exact_first_bad_commit_on_a_simple_history():
    commits, (result, visited), calls, skipped, _ = _run_p(20, 13, 3)
    assert result == commits[13] and skipped == []
    assert len(set(calls)) == len(calls)  # each commit probed at most once


def test_no_regression_in_range_returns_none():
    commits, (result, _), _, _, _ = _run_p(9, 9, 3)
    assert result is None


def test_exhaustive_small_histories_match_the_sequential_search():
    for n in range(1, 9):
        for k in range(0, n + 1):
            for p in (2, 3, 4):
                commits, (result, _), _, _, _ = _run_p(n, k, p)
                assert result == (commits[k] if k < n else None), (n, k, p)


def test_exhaustive_small_histories_with_skips_never_give_a_wrong_answer():
    for n in range(1, 7):
        for k in range(0, n + 1):
            for mask in itertools.product([False, True], repeat=n):
                unrunnable = {i for i, m in enumerate(mask) if m}
                commits = [f"c{i:03d}" for i in range(n)]
                for p in (2, 3):
                    try:
                        _, (result, _), _, _, _ = _run_p(n, k, p, unrunnable)
                    except BisectionAmbiguous as amb:
                        truth = commits[k] if k < n else None
                        assert truth is None or truth in amb.suspects or truth == amb.boundary
                        continue
                    assert result == (commits[k] if k < n else None), (n, k, p, unrunnable)


@pytest.mark.parametrize("seed", range(25))
def test_property_fewer_rounds_than_sequential_and_bounded_probes(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 200)
    k = rng.randint(0, n)
    p = rng.randint(2, 6)
    commits, (result, _), calls, _, rounds = _run_p(n, k, p)
    assert result == (commits[k] if k < n else None)
    n_rounds = sum(1 for ev, *_ in rounds if ev == "start")
    assert n_rounds <= math.ceil(math.log(n + 1, p + 1)) + 1
    assert len(calls) <= n_rounds * p
    # sequential bisection needs ~log2(n) dependent runs; this needs fewer rounds for p >= 2
    assert n_rounds <= max(1, math.ceil(math.log2(n + 1)))


def test_rounds_emit_start_then_done_with_a_shrinking_window():
    _, _, _, _, rounds = _run_p(30, 21, 3)
    kinds = [r[0] for r in rounds]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert kinds[::2] == ["start"] * (len(kinds) // 2) and kinds[1::2] == ["done"] * (len(kinds) // 2)
    sizes = [hi - lo + 1 for ev, lo, hi, _ in rounds if ev == "done"]
    assert sizes == sorted(sizes, reverse=True)


def test_probes_in_a_round_really_run_concurrently_and_never_exceed_p():
    commits = [f"c{i:03d}" for i in range(26)]
    lock = threading.Lock()
    live = {"now": 0, "max": 0}

    def evaluate(sha):
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        time.sleep(0.05)
        with lock:
            live["now"] -= 1
        return _ev("regressed" if int(sha[1:]) >= 17 else "clean")

    result, _ = bisect_parallel(commits, evaluate=evaluate, parallelism=3)
    assert result == commits[17]
    assert live["max"] == 3


def test_skips_in_a_round_are_recorded_and_the_search_goes_on():
    commits, (result, _), calls, skipped, _ = _run_p(15, 11, 3, {6})  # round 1 probes 2, 6, 10
    assert result == commits[11]
    assert [i for i, _ in skipped] == [6]


def test_when_every_suspect_is_unrunnable_it_says_so_instead_of_guessing():
    with pytest.raises(BisectionAmbiguous) as e:
        _run_p(8, 4, 3, {3, 4})  # 3 is the last clean, 4 the first bad -> both unrunnable
    assert e.value.suspects


def test_too_many_skips_gives_up():
    with pytest.raises(BisectionError):
        _run_p(40, 30, 3, set(range(0, 40)), max_skips=4)


def test_a_failing_probe_is_raised_only_after_the_rest_of_its_round_finished():
    commits = [f"c{i:03d}" for i in range(12)]
    finished = []

    def evaluate(sha):
        if sha == "c002":
            raise RuntimeError("sandbox exploded")
        time.sleep(0.15)
        finished.append(sha)
        return _ev("clean")

    with pytest.raises(RuntimeError, match="exploded"):
        bisect_parallel(commits, evaluate=evaluate, parallelism=3)
    assert set(finished) == {"c005", "c008"}  # nothing left running in the background


def test_parallelism_1_through_bisect_is_untouched():
    commits = [f"c{i:03d}" for i in range(9)]
    res, _ = bisect(commits, evaluate=lambda s: _ev("regressed" if int(s[1:]) >= 6 else "clean"))
    assert res == commits[6]


# ---------------------------------------------------------------------------
# Layer B — real git + real sandbox, fake Nano
# ---------------------------------------------------------------------------

class _Rep(bisector.Reporter):
    def __init__(self, events):
        super().__init__("j", lambda ev: events.append(ev), None)


def test_bisect_repo_with_parallel_probes_finds_the_same_commit_and_narrates_rounds(tmp_path, monkeypatch):
    monkeypatch.setattr(bisector, "call_nemotron", _fake_nano)
    repo, shas = _make_repo(tmp_path, [FAST] * 5 + [SLOW] * 7)  # guilty = index 5, 12 commits
    events = []
    result = bisector.bisect_repo(
        job_id="j", repo_path=str(repo), start_sha=shas[0], end_sha=shas[-1], benchmark_command="python bench.py",
        sandbox=LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache")), n_runs=3,
        reporter=_Rep(events), parallelism=3,
    )
    assert result.regression_commit == shas[5]
    kinds = [e.kind for e in events]
    assert "bisect.round" in kinds
    plan = next(e for e in events if e.kind == "bisect.plan")
    assert plan.data["parallelism"] == 3 and plan.data["rounds"] >= 1
    done = next(e for e in events if e.kind == "bisect.done")
    assert done.data["parallelism"] == 3 and done.data["rounds"] >= 1
    assert done.data["probe_time_s"] >= done.data["wall_s"] * 0.5  # probes overlapped, they didn't queue
    # every probe is attributed to its own commit and the timeline reads oldest -> newest
    commits_probed = [p.commit for p in result.probes]
    assert len(commits_probed) == len(set(commits_probed))
    order = {s: i for i, s in enumerate(shas)}
    assert [order[t.commit] for t in result.timeline] == sorted(order[t.commit] for t in result.timeline)


def test_parallel_bisect_repo_steps_over_a_broken_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(bisector, "call_nemotron", _fake_nano)
    repo, shas = _make_repo(tmp_path, [FAST, FAST, SLOW, BROKEN, SLOW, SLOW, SLOW, SLOW])
    result = bisector.bisect_repo(
        job_id="j", repo_path=str(repo), start_sha=shas[0], end_sha=shas[-1], benchmark_command="python bench.py",
        sandbox=LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache")), n_runs=3,
        reporter=_Rep([]), parallelism=3,
    )
    assert result.regression_commit == shas[2]
    assert [s.sha for s in result.skipped] == [shas[3]]


def test_sequential_default_emits_no_round_events(tmp_path, monkeypatch):
    monkeypatch.setattr(bisector, "call_nemotron", _fake_nano)
    repo, shas = _make_repo(tmp_path, [FAST, FAST, SLOW, SLOW, SLOW])
    events = []
    result = bisector.bisect_repo(
        job_id="j", repo_path=str(repo), start_sha=shas[0], end_sha=shas[-1], benchmark_command="python bench.py",
        sandbox=LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache")), n_runs=3, reporter=_Rep(events),
    )
    assert result.regression_commit == shas[2]
    assert "bisect.round" not in [e.kind for e in events]
    assert next(e for e in events if e.kind == "bisect.plan").data["parallelism"] == 1


# ---------------------------------------------------------------------------
# API / config
# ---------------------------------------------------------------------------

def test_parallelism_resolution(monkeypatch):
    monkeypatch.setattr(api, "_BISECT_PARALLELISM", "auto")
    assert api._resolve_parallelism(True) == 3 and api._resolve_parallelism(False) == 1
    monkeypatch.setattr(api, "_BISECT_PARALLELISM", "4")
    assert api._resolve_parallelism(True) == 4 and api._resolve_parallelism(False) == 4
    monkeypatch.setattr(api, "_BISECT_PARALLELISM", "99")
    assert api._resolve_parallelism(True) == api._MAX_PARALLELISM
    for junk in ("0", "-3", "lots"):
        monkeypatch.setattr(api, "_BISECT_PARALLELISM", junk)
        assert api._resolve_parallelism(True) == 1


def test_a_job_with_parallel_probes_on_the_local_sandbox_works_and_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(bisector, "call_nemotron", _fake_nano)
    monkeypatch.setattr(diagnoser, "call_nemotron", _fake_diagnoser_call_nemotron_other)
    monkeypatch.setattr(api, "_BISECT_PARALLELISM", "3")
    client = TestClient(create_app(store=InMemoryJobStore()))
    repo, shas = _make_repo(tmp_path, [FAST] * 4 + [SLOW] * 5)
    r = client.post("/analyze", json={"repo_url": str(repo), "benchmark_command": "python bench.py",
                                      "commit_range": [shas[0], shas[-1]]})
    job = _wait_for_terminal(client, r.json()["job_id"], timeout=120)
    assert job["status"] == "done", job.get("error")
    assert job["regression_commit"] == shas[4]
    events = client.get(f"/jobs/{job['job_id']}/events").json()["events"]
    warn = [e for e in events if e["kind"] == "bisect.warning"]
    assert len(warn) == 1 and "share this machine's CPU" in warn[0]["message"]
    assert any(e["kind"] == "bisect.round" for e in events)


def test_config_reports_the_parallelism(monkeypatch):
    monkeypatch.setattr(api, "_BISECT_PARALLELISM", "auto")
    client = TestClient(create_app(store=InMemoryJobStore()))
    assert client.get("/config").json()["bisect_parallelism"] == 1  # local sandbox by default


def test_local_sandbox_survives_many_concurrent_checkouts(tmp_path):
    """Regression: concurrent `git worktree add/remove` on one repo corrupted git's
    metadata ("failed to read .../commondir"). Checkouts are now serialized; the
    benchmarks themselves still run side by side."""
    from concurrent.futures import ThreadPoolExecutor
    repo, shas = _make_repo(tmp_path, [FAST] * 8)
    sb = LocalGitSandbox(str(repo), cache_dir=str(tmp_path / "cache"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(sb.run_benchmark, sha, "python bench.py", 1) for sha in shas]
        results = [f.result() for f in futs]  # any SandboxError propagates here
    assert [r.commit_sha for r in results] == shas and all(r.raw_scores for r in results)
