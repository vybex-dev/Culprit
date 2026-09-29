# backend/bisector.py
"""
bisector.py — binary search orchestration over commit history, per
AGENT_SPECS.md §1 / TRD.md §2.1 / BUILD_01 §4.

Deliberately split into two layers:

  Layer A (pure algorithm): `bisect()` + `evaluate_candidate()`. No sandbox,
  no Nemotron — every dependency is injected as a plain callable. This is
  what BUILD_01's "unit test the bisection logic against a synthetic mock
  commit history" and "stress-test noise handling" instructions are asking
  for: fast, deterministic, no sandbox/API cost, and it's the layer that
  contains the actual subtle logic (binary search + the inconclusive
  accumulate-and-re-ask loop).

  Layer B (real orchestration): `bisect_repo()`. Wires Layer A up to a real
  Sandbox (sandbox_client.py) and a real Nemotron Nano call (models.py).
  This is the ONLY place in the whole codebase that should ever call
  call_nemotron(model="nano", ...) to decide a verdict — AGENTS.md #4 is
  explicit that Nano's control-flow decisions are the model's job, not a
  hardcoded Python heuristic. (A reference decision function that mimics
  the documented Nano policy exists ONLY in tests/test_bisector.py, as a
  stand-in for Nano so the search algorithm itself is testable without a
  live API key — it is never imported here.)

Two engineering decisions BUILD_01/AGENT_SPECS.md don't pin down, flagged
rather than silently chosen:
  1. "commit_range" in every Nano payload is the ORIGINAL overall
     (start_sha, end_sha) for the whole job, not the shrinking bisection
     window — Nano's own system prompt doesn't use it to decide anything,
     it's there for traceability in the logs.
  2. What happens if a candidate stays "inconclusive" after several
     escalation rounds, or Nano's `additional_runs_needed` would blow past
     a sane total-runs budget. Spec is silent here; this module caps both
     (max_rounds, max_total_runs) and raises BisectionError rather than
     ever guessing a verdict — never claims a candidate is "resolved" when
     it isn't (AGENTS.md #1's spirit, applied to Nano calls, not just
     sandbox runs).
"""

from __future__ import annotations

import statistics
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal

import structlog

from models import call_nemotron
from sandbox_client import Sandbox

log = structlog.get_logger("bisector")

Verdict = Literal["regressed", "clean", "inconclusive"]

# Verbatim from AGENT_SPECS.md §1 — don't edit here without updating that doc
# first (AGENTS.md #3).
BISECTOR_SYSTEM_PROMPT = """You are a control-flow decision engine for a performance bisection tool.
You will be given a candidate commit's benchmark scores (multiple runs),
a baseline score, and a regression threshold percentage.

Decide:
1. Whether this candidate commit is "regressed" (median score is worse than
   baseline by more than the threshold), "clean", or "inconclusive" (noise
   too high to tell — e.g. run-to-run variance exceeds the threshold itself).
2. If inconclusive, recommend how many additional runs to collect.

Return ONLY JSON matching this schema:
{
  "verdict": "regressed" | "clean" | "inconclusive",
  "median_score": <number>,
  "pct_change_from_baseline": <number>,
  "additional_runs_needed": <integer, 0 if not inconclusive>
}"""


class BisectionError(Exception):
    """Raised when bisection can't proceed honestly: Nano returned a
    schema-invalid response, a candidate stayed inconclusive past the
    retry/runs budget, or the commit range is empty. Distinct from
    SandboxError/NemotronCallError, which propagate unwrapped from their
    own modules — callers can tell "the search logic gave up" apart from
    "the sandbox/API call itself failed"."""


@dataclass
class NanoVerdict:
    verdict: Verdict
    median_score: float
    pct_change_from_baseline: float
    additional_runs_needed: int


@dataclass
class CandidateEvaluation:
    verdict: Verdict  # always "regressed" or "clean" — evaluate_candidate()
    median_score: float  # resolves "inconclusive" internally or raises
    pct_change_from_baseline: float
    raw_scores: list[float]
    rounds: int  # how many ask-Nano rounds this candidate needed


@dataclass
class TimelineEntry:
    commit: str
    score: float
    timestamp: str


@dataclass
class BisectionResult:
    regression_commit: str | None  # None if no regression found in the range
    timeline: list[TimelineEntry]  # oldest -> newest; index 0 is start_sha
    baseline_score: float
    candidates_evaluated: int  # excludes the baseline run itself


# ---------------------------------------------------------------------------
# Layer A — pure algorithm
# ---------------------------------------------------------------------------

def evaluate_candidate(
    commit_sha: str,
    *,
    run_more: Callable[[str, int], list[float]],
    ask_nano: Callable[[str, list[float]], NanoVerdict],
    initial_n_runs: int,
    max_rounds: int = 3,
    max_total_runs: int = 20,
) -> CandidateEvaluation:
    """Runs initial_n_runs, asks Nano; on "inconclusive", collects the
    recommended additional runs and re-asks — accumulating scores across
    rounds, never discarding earlier ones — up to max_rounds total asks (and
    max_total_runs total sandbox runs). Raises BisectionError rather than
    ever returning an unresolved "inconclusive" candidate."""
    scores = list(run_more(commit_sha, initial_n_runs))
    round_num = 0
    for round_num in range(1, max_rounds + 1):
        decision = ask_nano(commit_sha, scores)
        if decision.verdict != "inconclusive":
            return CandidateEvaluation(
                verdict=decision.verdict, median_score=decision.median_score,
                pct_change_from_baseline=decision.pct_change_from_baseline,
                raw_scores=scores, rounds=round_num,
            )
        if round_num == max_rounds:
            break  # no asks left to spend on more data — stop escalating
        n_more = min(decision.additional_runs_needed, max_total_runs - len(scores))
        if n_more <= 0:
            break  # Nano wants no more data, or we've hit the runs budget
        scores += list(run_more(commit_sha, n_more))
    raise BisectionError(
        f"{commit_sha} stayed inconclusive after {round_num} round(s) / "
        f"{len(scores)} runs — refusing to guess a verdict"
    )


def bisect(
    commits: list[str],
    *,
    evaluate: Callable[[str], CandidateEvaluation],
) -> tuple[str | None, list[tuple[int, CandidateEvaluation]]]:
    """commits: oldest -> newest, assumed monotonic (clean...clean then
    regressed...regressed — the standard bisection precondition). Binary
    search for the leftmost "regressed" index. Returns
    (regression_commit_or_None, [(index, evaluation), ...] for every commit
    actually evaluated — O(log n), not every commit in the range)."""
    n = len(commits)
    lo, hi = 0, n - 1
    result: str | None = None
    visited: list[tuple[int, CandidateEvaluation]] = []
    while lo <= hi:
        mid = (lo + hi) // 2
        ev = evaluate(commits[mid])
        visited.append((mid, ev))
        if ev.verdict == "regressed":
            result = commits[mid]
            hi = mid - 1
        else:  # "clean" — evaluate_candidate() already resolved "inconclusive"
            lo = mid + 1
    return result, visited


# ---------------------------------------------------------------------------
# Layer B — real orchestration: real Sandbox + real Nemotron Nano
# ---------------------------------------------------------------------------

def list_commits_between(repo_path: str, start_sha: str, end_sha: str) -> list[str]:
    """Oldest -> newest, excluding start_sha, including end_sha — mirrors
    git's own `start..end` range convention."""
    try:
        proc = subprocess.run(
            ["git", "rev-list", "--reverse", f"{start_sha}..{end_sha}"],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired as e:
        # Same pattern already proven correct in sandbox_client.py — see
        # CODE_REVIEW_FINDINGS.md #7: this call was one of two places that
        # hadn't been carried forward as a convention yet.
        raise BisectionError(f"git rev-list timed out (30s) for {start_sha}..{end_sha}") from e
    if proc.returncode != 0:
        raise BisectionError(f"git rev-list failed for {start_sha}..{end_sha}: {proc.stderr}")
    commits = [c for c in proc.stdout.splitlines() if c.strip()]
    if not commits:
        raise BisectionError(f"no commits found between {start_sha} and {end_sha}")
    return commits


def _validate_nano_verdict(parsed: dict, *, commit_sha: str) -> NanoVerdict:
    """models.py only guarantees the response IS valid JSON — not that it
    matches this schema. A syntactically valid but wrong-shaped object must
    still fail loud here, or a coincidentally-JSON-shaped hallucination
    could silently steer the whole search."""
    required = {"verdict", "median_score", "pct_change_from_baseline", "additional_runs_needed"}
    missing = required - parsed.keys()
    if missing:
        raise BisectionError(f"Nano response for {commit_sha} missing fields {sorted(missing)}: {parsed!r}")
    if parsed["verdict"] not in ("regressed", "clean", "inconclusive"):
        raise BisectionError(f"Nano returned unknown verdict {parsed['verdict']!r} for {commit_sha}")
    try:
        return NanoVerdict(
            verdict=parsed["verdict"],
            median_score=float(parsed["median_score"]),
            pct_change_from_baseline=float(parsed["pct_change_from_baseline"]),
            additional_runs_needed=int(parsed["additional_runs_needed"]),
        )
    except (TypeError, ValueError) as e:
        raise BisectionError(f"Nano response for {commit_sha} has wrong field types: {parsed!r}") from e


def _reconcile_with_arithmetic(
    nano: NanoVerdict, *, scores: list[float], baseline_score: float,
    threshold_pct: float, job_id: str, commit_sha: str,
) -> NanoVerdict:
    """Nano still owns "inconclusive" and how many more runs to collect
    (AGENTS.md #4). But "regressed"/"clean" is pure arithmetic — median vs
    baseline against the threshold — and the small model has been seen
    calling +2.5% "regressed" against a 15% threshold. If its verdict
    contradicts the arithmetic, override it with the arithmetic result and
    log loudly, so a wrong verdict can't silently steer the search."""
    if baseline_score <= 0:
        return nano
    median = statistics.median(scores)
    pct = (median - baseline_score) / baseline_score * 100.0
    expected: Verdict = "regressed" if pct > threshold_pct else "clean"

    if nano.verdict == "inconclusive":
        # "Inconclusive" is only legitimate when noise could flip the verdict.
        # If EVERY run (min..max) lands on the same side of the threshold as
        # the median, the data is decisive no matter how noisy Nano thinks it
        # is. Once we have a reasonable sample, override a spurious
        # "inconclusive" with the arithmetic result.
        if len(scores) < 5:
            return nano
        lo_pct = (min(scores) - baseline_score) / baseline_score * 100.0
        hi_pct = (max(scores) - baseline_score) / baseline_score * 100.0
        decisive = hi_pct <= threshold_pct or lo_pct > threshold_pct
        if not decisive:
            return nano
        log.warning(
            "bisector.nano_inconclusive_overridden", job_id=job_id, commit=commit_sha,
            arithmetic_verdict=expected, median_score=median, pct_change=pct,
            min_pct=lo_pct, max_pct=hi_pct, threshold_pct=threshold_pct, n_runs=len(scores),
        )
        return NanoVerdict(
            verdict=expected, median_score=median,
            pct_change_from_baseline=pct, additional_runs_needed=0,
        )
    if nano.verdict == expected:
        return nano
    log.warning(
        "bisector.nano_verdict_overridden", job_id=job_id, commit=commit_sha,
        nano_verdict=nano.verdict, arithmetic_verdict=expected,
        median_score=median, pct_change=pct, threshold_pct=threshold_pct,
    )
    return NanoVerdict(
        verdict=expected, median_score=median,
        pct_change_from_baseline=pct, additional_runs_needed=0,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bisect_repo(
    *,
    job_id: str,
    repo_path: str,
    start_sha: str,
    end_sha: str,
    benchmark_command: str,
    sandbox: Sandbox,
    regression_threshold_pct: float = 15.0,
    n_runs: int = 5,
    nano_temperature: float = 0.2,
) -> BisectionResult:
    """The real thing: enumerates commits, benchmarks start_sha as the fixed
    baseline, binary-searches the rest via real sandbox runs + real Nano
    calls. Propagates SandboxError / NemotronCallError unwrapped from their
    own modules; raises BisectionError for search-logic-specific failures.
    """
    commits = list_commits_between(repo_path, start_sha, end_sha)

    log.info("bisector.start", job_id=job_id, start_sha=start_sha, end_sha=end_sha,
              candidate_count=len(commits), regression_threshold_pct=regression_threshold_pct)

    baseline_result = sandbox.run_benchmark(start_sha, benchmark_command, n_runs=n_runs)
    baseline_score = statistics.median(baseline_result.raw_scores)

    timeline: list[TimelineEntry] = [
        TimelineEntry(commit=start_sha, score=baseline_score, timestamp=_now_iso())
    ]

    def _run_more(commit_sha: str, n: int) -> list[float]:
        return sandbox.run_benchmark(commit_sha, benchmark_command, n_runs=n).raw_scores

    def _ask_nano(commit_sha: str, scores: list[float]) -> NanoVerdict:
        payload = {
            "commit_range": [start_sha, end_sha],  # overall range — see module docstring
            "candidate_sha": commit_sha,
            "candidate_scores": scores,
            "baseline_score": baseline_score,
            "regression_threshold_pct": regression_threshold_pct,
        }
        result = call_nemotron(
            job_id=job_id, step=f"bisect:candidate={commit_sha[:10]}",
            model="nano", system_prompt=BISECTOR_SYSTEM_PROMPT,
            payload=payload, temperature=nano_temperature,
        )
        nano = _validate_nano_verdict(result.parsed, commit_sha=commit_sha)
        return _reconcile_with_arithmetic(
            nano, scores=scores, baseline_score=baseline_score,
            threshold_pct=regression_threshold_pct, job_id=job_id, commit_sha=commit_sha,
        )

    def _evaluate(commit_sha: str) -> CandidateEvaluation:
        ev = evaluate_candidate(commit_sha, run_more=_run_more, ask_nano=_ask_nano, initial_n_runs=n_runs)
        timeline.append(TimelineEntry(commit=commit_sha, score=ev.median_score, timestamp=_now_iso()))
        log.info("bisector.candidate_evaluated", job_id=job_id, commit=commit_sha,
                  verdict=ev.verdict, median_score=ev.median_score,
                  pct_change=ev.pct_change_from_baseline, rounds=ev.rounds)
        return ev

    regression_commit, visited = bisect(commits, evaluate=_evaluate)

    # Sort by position in the overall commit order, not "order visited during
    # the search" — the dashboard timeline should read oldest -> newest.
    order = {sha: i for i, sha in enumerate([start_sha] + commits)}
    timeline.sort(key=lambda e: order[e.commit])

    log.info("bisector.result", job_id=job_id, regression_commit=regression_commit,
              candidates_evaluated=len(visited), baseline_score=baseline_score)

    return BisectionResult(
        regression_commit=regression_commit, timeline=timeline,
        baseline_score=baseline_score, candidates_evaluated=len(visited),
    )
