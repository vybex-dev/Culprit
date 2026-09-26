# backend/tests/test_bisector.py
"""
Per BUILD_01: unit test the bisection logic against a synthetic mock commit
history first (fast, no sandbox/API cost), then stress-test noise handling.

_reference_decision() below is a TEST-ONLY stand-in for what a well-behaved
Nano should return, based on the documented policy in AGENT_SPECS.md §1. It
is never imported into bisector.py — production always calls the real Nano
model via models.call_nemotron. It exists purely so the search algorithm and
the inconclusive-escalation policy are testable without a live API key.
"""

import json
import math
import random
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bisector  # noqa: E402  (module import so monkeypatch.setattr works)
from bisector import BisectionError, NanoVerdict, bisect, evaluate_candidate  # noqa: E402
from models import NemotronResult  # noqa: E402
from sandbox_client import LocalGitSandbox  # noqa: E402


def _reference_decision(scores: list[float], baseline_score: float, regression_threshold_pct: float) -> dict:
    median = statistics.median(scores)
    pct_change = (median - baseline_score) / baseline_score * 100
    if len(scores) < 2:
        return {
            "verdict": "inconclusive", "median_score": median,
            "pct_change_from_baseline": pct_change, "additional_runs_needed": 5,
        }
    # Standard error (not raw range — range stays ~constant regardless of n
    # for a fixed noise distribution, so it never shrinks as more runs come
    # in; SE does, which is what makes escalation actually converge).
    se_pct = (statistics.stdev(scores) / baseline_score * 100) / math.sqrt(len(scores))
    ci_lower, ci_upper = pct_change - 2 * se_pct, pct_change + 2 * se_pct
    if ci_lower < regression_threshold_pct < ci_upper:
        return {
            "verdict": "inconclusive", "median_score": median,
            "pct_change_from_baseline": pct_change, "additional_runs_needed": 5,
        }
    verdict = "regressed" if pct_change > regression_threshold_pct else "clean"
    return {
        "verdict": verdict, "median_score": median,
        "pct_change_from_baseline": pct_change, "additional_runs_needed": 0,
    }


def make_synthetic_history(n_commits, regression_index, baseline_score=100.0, regressed_score=150.0,
                            noise_pct=0.0, seed=0):
    """commits[i] has 'true' score regressed_score for i >= regression_index,
    else baseline_score. regression_index == n_commits means no regression
    exists anywhere in the (synthetic) history."""
    rng = random.Random(seed)
    commits = [f"c{i:03d}" for i in range(n_commits)]
    true_score = {c: (regressed_score if i >= regression_index else baseline_score) for i, c in enumerate(commits)}

    def run_more(commit_sha, n):
        base = true_score[commit_sha]
        return [base * (1 + rng.uniform(-noise_pct, noise_pct)) for _ in range(n)]

    return commits, run_more


# ---------------------------------------------------------------------------
# Layer A: pure algorithm — bisect()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n,k", [
    (1, 0), (1, 1),
    (2, 0), (2, 1), (2, 2),
    (8, 0), (8, 3), (8, 7), (8, 8),
    (16, 5), (16, 0), (16, 15), (16, 16),
])
def test_bisection_converges_to_exact_regression_commit(n, k):
    commits, run_more = make_synthetic_history(n, k, noise_pct=0.0)

    def ask_nano(commit_sha, scores):
        return NanoVerdict(**_reference_decision(scores, baseline_score=100.0, regression_threshold_pct=15.0))

    def evaluate(commit_sha):
        return evaluate_candidate(commit_sha, run_more=run_more, ask_nano=ask_nano, initial_n_runs=3)

    regression_commit, visited = bisect(commits, evaluate=evaluate)
    expected = commits[k] if k < n else None
    assert regression_commit == expected
    assert len(visited) <= math.ceil(math.log2(n)) + 1  # definition of done: O(log n), not O(n)


# ---------------------------------------------------------------------------
# Layer A: evaluate_candidate()'s inconclusive-escalation policy
# ---------------------------------------------------------------------------

def test_evaluate_candidate_accumulates_runs_on_inconclusive():
    run_more_calls = []

    def run_more(commit_sha, n):
        run_more_calls.append(n)
        return [0.0] * n

    responses = iter([
        NanoVerdict(verdict="inconclusive", median_score=0, pct_change_from_baseline=0, additional_runs_needed=4),
        NanoVerdict(verdict="clean", median_score=100, pct_change_from_baseline=1.0, additional_runs_needed=0),
    ])
    seen_lengths = []

    def ask_nano(commit_sha, scores):
        seen_lengths.append(len(scores))
        return next(responses)

    result = evaluate_candidate("cX", run_more=run_more, ask_nano=ask_nano, initial_n_runs=3)
    assert result.verdict == "clean"
    assert result.rounds == 2
    assert seen_lengths == [3, 7]  # 3 initial + 4 additional = 7, never reset to just 4
    assert run_more_calls == [3, 4]


def test_evaluate_candidate_raises_after_max_rounds_still_inconclusive():
    def run_more(commit_sha, n):
        return [100.0] * n

    def ask_nano(commit_sha, scores):
        return NanoVerdict(verdict="inconclusive", median_score=100, pct_change_from_baseline=0,
                            additional_runs_needed=2)

    with pytest.raises(BisectionError):
        evaluate_candidate("cX", run_more=run_more, ask_nano=ask_nano, initial_n_runs=3, max_rounds=3)


def test_evaluate_candidate_stops_if_nano_requests_zero_more_runs():
    call_count = 0

    def run_more(commit_sha, n):
        nonlocal call_count
        call_count += 1
        return [100.0] * n

    def ask_nano(commit_sha, scores):
        return NanoVerdict(verdict="inconclusive", median_score=100, pct_change_from_baseline=0,
                            additional_runs_needed=0)

    with pytest.raises(BisectionError):
        evaluate_candidate("cX", run_more=run_more, ask_nano=ask_nano, initial_n_runs=3)
    assert call_count == 1  # only the initial run — no point escalating for zero more runs


def test_evaluate_candidate_respects_max_total_runs_cap():
    requested = []

    def run_more(commit_sha, n):
        requested.append(n)
        return [100.0] * n

    def ask_nano(commit_sha, scores):
        return NanoVerdict(verdict="inconclusive", median_score=100, pct_change_from_baseline=0,
                            additional_runs_needed=100)

    with pytest.raises(BisectionError):
        evaluate_candidate("cX", run_more=run_more, ask_nano=ask_nano, initial_n_runs=3,
                            max_rounds=5, max_total_runs=10)
    assert sum(requested) <= 10


# ---------------------------------------------------------------------------
# Noise stress test — NFR: correct despite +/-10% benchmark noise
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_stable_commit_never_falsely_flagged_regressed_under_noise(seed):
    n = 20  # regression_index == n -> no regression exists anywhere
    commits, run_more = make_synthetic_history(n, regression_index=n, noise_pct=0.10, seed=seed)

    def ask_nano(commit_sha, scores):
        return NanoVerdict(**_reference_decision(scores, baseline_score=100.0, regression_threshold_pct=15.0))

    def evaluate(commit_sha):
        return evaluate_candidate(commit_sha, run_more=run_more, ask_nano=ask_nano, initial_n_runs=5)

    regression_commit, visited = bisect(commits, evaluate=evaluate)
    assert regression_commit is None
    assert all(ev.verdict == "clean" for _, ev in visited)  # never a false-positive "regressed"


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_regression_still_found_despite_noise(seed):
    n, k = 20, 12
    commits, run_more = make_synthetic_history(n, regression_index=k, noise_pct=0.10, seed=seed)

    def ask_nano(commit_sha, scores):
        return NanoVerdict(**_reference_decision(scores, baseline_score=100.0, regression_threshold_pct=15.0))

    def evaluate(commit_sha):
        return evaluate_candidate(commit_sha, run_more=run_more, ask_nano=ask_nano, initial_n_runs=5)

    regression_commit, _ = bisect(commits, evaluate=evaluate)
    assert regression_commit == commits[k]  # noise shouldn't bury a real regression either


# ---------------------------------------------------------------------------
# Layer B: real sandbox + faked Nano — proves the orchestration wiring itself
# ---------------------------------------------------------------------------

def test_bisect_repo_end_to_end_real_sandbox_fake_nano(tmp_path, monkeypatch):
    """Real git repo, real LocalGitSandbox subprocess benchmark runs; Nano is
    faked (no live API key in this environment) with the same reference
    decision used above. Proves git rev-list, baseline computation, payload
    plumbing, and timeline sorting all work together end-to-end — not just
    the algorithm in isolation."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def run(*a):
        subprocess.run(a, cwd=repo, check=True, capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@e.com")
    run("git", "config", "user.name", "T")

    fast_bench = (
        "import time\nstart=time.perf_counter()\ntotal=0\n"
        "for i in range(500_000): total+=i\nprint((time.perf_counter()-start)*1000)\n"
    )
    slow_bench = (
        "import time\nstart=time.perf_counter()\ntotal=0\n"
        "for _ in range(6):\n    for i in range(500_000): total+=i\n"
        "print((time.perf_counter()-start)*1000)\n"
    )

    shas = []
    for idx, content in enumerate([fast_bench, fast_bench, slow_bench, slow_bench, slow_bench]):
        (repo / "bench.py").write_text(f"# commit {idx}\n{content}")
        run("git", "add", ".")
        run("git", "commit", "-q", "-m", f"commit {idx}")
        shas.append(
            subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
        )

    start_sha, end_sha = shas[0], shas[-1]

    def fake_call_nemotron(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
        d = _reference_decision(payload["candidate_scores"], payload["baseline_score"], payload["regression_threshold_pct"])
        return NemotronResult(parsed=d, raw_response=json.dumps(d), attempts=1, model_id="fake-nano", latency_s=0.0)

    monkeypatch.setattr(bisector, "call_nemotron", fake_call_nemotron)

    sandbox = LocalGitSandbox(str(repo))
    result = bisector.bisect_repo(
        job_id="j1", repo_path=str(repo), start_sha=start_sha, end_sha=end_sha,
        benchmark_command="python bench.py", sandbox=sandbox, n_runs=3,
    )

    assert result.regression_commit == shas[2]  # first of the three slow commits
    assert result.timeline[0].commit == start_sha
    order = {sha: i for i, sha in enumerate(shas)}
    positions = [order[e.commit] for e in result.timeline]
    assert positions == sorted(positions)  # timeline reads oldest -> newest, not visit order
