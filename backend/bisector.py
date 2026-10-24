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

import math
import statistics
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import structlog

from events import Reporter, or_null, sandbox_listener
from models import call_nemotron
from sandbox_client import BenchmarkRunError, DependencyInstallError, Sandbox

log = structlog.get_logger("bisector")

Verdict = Literal["regressed", "clean", "inconclusive"]

# Share of runs that must fall on the median's side of the regression threshold
# for the data to count as decisive despite a few outlier runs (see
# _reconcile_with_arithmetic).
DECISIVE_AGREEMENT = 0.75

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


class CommitUnrunnable(Exception):
    """One commit can't be benchmarked (build/dependency/benchmark failure at
    that revision). Raised by an `evaluate` callable, handled inside bisect():
    like `git bisect skip`, the search steps to a neighbouring commit instead
    of guessing a verdict for this one."""

    def __init__(self, commit: str, reason: str = "") -> None:
        super().__init__(f"{commit[:10]} could not be run: {reason}" if reason else f"{commit[:10]} could not be run")
        self.commit = commit
        self.reason = reason


class BisectionAmbiguous(BisectionError):
    """Skipped commits made it impossible to name ONE guilty commit: every
    commit still under suspicion is unrunnable. Reported honestly (with the
    suspects) rather than picking one — the same answer `git bisect` gives."""

    def __init__(self, suspects: list[str], boundary: str | None) -> None:
        shown = ", ".join(s[:8] for s in suspects[:8]) + (f" … (+{len(suspects) - 8} more)" if len(suspects) > 8 else "")
        super().__init__(
            f"the regression is in one of {len(suspects)} commit(s) that could not be run ({shown})"
            + (f", or in {boundary[:8]}, the first commit known to be regressed" if boundary else "")
            + ". Fix the build/benchmark at those commits, or narrow the range past them."
        )
        self.suspects = suspects
        self.boundary = boundary


@dataclass
class SkippedCommit:
    """A commit the search had to step over because it couldn't be run."""
    index: int
    sha: str
    reason: str
    step: int = 0


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
class CommitInfo:
    """One commit in the searched range (index 0 is the known-good start)."""
    index: int
    sha: str
    subject: str = ""
    author: str = ""
    date: str = ""


@dataclass
class ProbeRecord:
    """Everything measured about one benchmarked commit — the raw material
    for the dashboard's scanner, run-scatter and terminal."""
    step: int  # 0 = baseline, then 1.. in the order probed
    commit: str
    index: int  # position in the [start] + commits list
    role: Literal["baseline", "endpoint", "bisect"]
    raw_scores: list[float]
    median_score: float
    pct_change: float
    verdict: str  # "baseline" | "clean" | "regressed"
    rounds: int
    nano: list[dict[str, Any]] = field(default_factory=list)  # one entry per Nano ask
    window: tuple[int, int] | None = None  # (lo, hi) global indices still under suspicion afterwards
    timestamp: str = ""
    wall_s: float = 0.0


@dataclass
class BisectionResult:
    regression_commit: str | None  # None if no regression found in the range
    timeline: list[TimelineEntry]  # oldest -> newest; index 0 is start_sha
    baseline_score: float
    candidates_evaluated: int  # excludes the baseline run itself
    probes: list[ProbeRecord] = field(default_factory=list)
    commits: list[CommitInfo] = field(default_factory=list)
    skipped: list[SkippedCommit] = field(default_factory=list)  # unrunnable commits the search stepped over


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


def _nearest_unskipped(mid: int, lo: int, hi: int, skipped: set[int]) -> int | None:
    """The index closest to `mid` inside [lo, hi] that hasn't been skipped
    (mid, mid+1, mid-1, mid+2, ...) — `git bisect skip` picks the same way."""
    for d in range(0, hi - lo + 1):
        for cand in ((mid,) if d == 0 else (mid + d, mid - d)):
            if lo <= cand <= hi and cand not in skipped:
                return cand
    return None


def bisect(
    commits: list[str],
    *,
    evaluate: Callable[[str], CandidateEvaluation],
    on_step: Callable[[str, int, int, int, CandidateEvaluation | None], None] | None = None,
    skipped_out: list[tuple[int, CommitUnrunnable]] | None = None,
    max_skips: int = 12,
) -> tuple[str | None, list[tuple[int, CandidateEvaluation]]]:
    """commits: oldest -> newest, assumed monotonic (clean...clean then
    regressed...regressed — the standard bisection precondition). Binary
    search for the leftmost "regressed" index. Returns
    (regression_commit_or_None, [(index, evaluation), ...] for every commit
    actually evaluated — O(log n), not every commit in the range).

    on_step(event, lo, hi, mid, ev): optional progress hook, called with
    event="probe" just before `mid` is evaluated (ev=None) and event="narrowed"
    right after, with the *new* window (lo, hi). Pure observation — it can't
    influence the search.

    `evaluate` may raise CommitUnrunnable. That commit is then skipped, as in
    `git bisect skip`: the search probes the nearest runnable neighbour in the
    window instead (on_step is called with event="skipped"; the commit is
    appended to `skipped_out` if given). If every commit still under suspicion
    turns out to be unrunnable, no single guilty commit can be named and
    BisectionAmbiguous is raised with the suspects — never a guess. With no
    skips this behaves exactly as the plain binary search always did."""
    n = len(commits)
    lo, hi = 0, n - 1
    result: str | None = None
    visited: list[tuple[int, CandidateEvaluation]] = []
    skipped: set[int] = set()
    while lo <= hi:
        pick = _nearest_unskipped((lo + hi) // 2, lo, hi, skipped)
        if pick is None:
            # Everything left in the window is unrunnable: the first regressed
            # commit is one of them, or the known-regressed one just after.
            raise BisectionAmbiguous([commits[i] for i in range(lo, hi + 1)], result)
        if on_step:
            on_step("probe", lo, hi, pick, None)
        try:
            ev = evaluate(commits[pick])
        except CommitUnrunnable as skip:
            skipped.add(pick)
            if skipped_out is not None:
                skipped_out.append((pick, skip))
            if len(skipped) > max_skips:
                raise BisectionError(
                    f"gave up after {len(skipped)} unrunnable commits — "
                    "the build or benchmark looks broken across this range"
                ) from skip
            if on_step:
                on_step("skipped", lo, hi, pick, None)
            continue
        visited.append((pick, ev))
        if ev.verdict == "regressed":
            result = commits[pick]
            hi = pick - 1
        else:  # "clean" — evaluate_candidate() already resolved "inconclusive"
            lo = pick + 1
        if on_step:
            on_step("narrowed", lo, hi, pick, ev)
    return result, visited


def _spread_picks(lo: int, hi: int, parallelism: int, skipped: set[int]) -> list[int]:
    """Up to `parallelism` distinct, unskipped indices spread evenly across
    [lo, hi] — they cut the window into parallelism+1 roughly equal parts, so
    one round narrows it by that factor (plain bisection is the P=1 case).
    When the window is no bigger than P, every runnable commit in it is probed
    and the round settles the answer outright. A target that was skipped is
    replaced by its nearest runnable neighbour."""
    size = hi - lo + 1
    if size <= parallelism:
        return [i for i in range(lo, hi + 1) if i not in skipped]
    picks: list[int] = []
    for j in range(parallelism):
        target = lo + ((j + 1) * size) // (parallelism + 1) - 1
        target = min(max(target, lo), hi)
        cand = _nearest_unskipped(target, lo, hi, skipped | set(picks))
        if cand is not None:
            picks.append(cand)
    return sorted(picks)


def bisect_parallel(
    commits: list[str],
    *,
    evaluate: Callable[[str], CandidateEvaluation],
    parallelism: int,
    on_round: Callable[[str, int, int, list[int], dict[int, CandidateEvaluation]], None] | None = None,
    skipped_out: list[tuple[int, CommitUnrunnable]] | None = None,
    max_skips: int = 12,
) -> tuple[str | None, list[tuple[int, CandidateEvaluation]]]:
    """k-ary search: the same contract and answer as bisect(), but each round
    evaluates up to `parallelism` commits CONCURRENTLY, so a window shrinks by a
    factor of parallelism+1 per round instead of 2 — fewer sequential rounds
    (wall-clock) for more total probes (sandbox cost). Worth it only when the
    probes don't disturb each other's timing, i.e. one isolated VM each.

    `evaluate` must be thread-safe (each call gets its own sandbox). Skips
    (CommitUnrunnable), ambiguity (BisectionAmbiguous) and the max_skips cap
    behave exactly as in bisect(). Any other exception from a probe — a real
    failure, cancellation — is raised once the round's other probes have
    finished; nothing is left running in the background.

    on_round(event, lo, hi, picks, results): "start" just before a round's
    probes launch (results empty), "done" right after, with the NEW window
    (lo, hi). Pure observation."""
    if parallelism < 2:
        raise ValueError("bisect_parallel needs parallelism >= 2 (use bisect() for a sequential search)")
    n = len(commits)
    lo, hi = 0, n - 1
    result: str | None = None
    visited: list[tuple[int, CandidateEvaluation]] = []
    skipped: set[int] = set()
    with ThreadPoolExecutor(max_workers=parallelism, thread_name_prefix="culprit-probe") as pool:
        while lo <= hi:
            picks = _spread_picks(lo, hi, parallelism, skipped)
            if not picks:
                raise BisectionAmbiguous([commits[i] for i in range(lo, hi + 1)], result)
            if on_round:
                on_round("start", lo, hi, picks, {})
            futures = {i: pool.submit(evaluate, commits[i]) for i in picks}
            results: dict[int, CandidateEvaluation] = {}
            first_error: BaseException | None = None
            for i in picks:  # wait for ALL of them, even after a failure
                try:
                    results[i] = futures[i].result()
                except CommitUnrunnable as skip:
                    skipped.add(i)
                    if skipped_out is not None:
                        skipped_out.append((i, skip))
                except BaseException as e:  # noqa: BLE001 — re-raised below, after the round settles
                    first_error = first_error or e
            if first_error is not None:
                raise first_error
            if len(skipped) > max_skips:
                raise BisectionError(
                    f"gave up after {len(skipped)} unrunnable commits — "
                    "the build or benchmark looks broken across this range"
                )
            visited.extend(sorted(results.items()))
            regressed = [i for i, ev in results.items() if ev.verdict == "regressed"]
            clean = [i for i, ev in results.items() if ev.verdict != "regressed"]
            if regressed:
                first = min(regressed)
                result = commits[first]
                hi = first - 1
                lo = max([i + 1 for i in clean if i < first], default=lo)
            elif clean:
                lo = max(i + 1 for i in clean)
            if on_round:
                on_round("done", lo, hi, picks, results)
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
        # The verdict is about the MEDIAN, and a median only flips if a large
        # share of runs sit on the other side of the threshold. A lone
        # cold-start/GC outlier (e.g. one 80 ms run among ten ~10 ms runs) must
        # not veto an otherwise unambiguous result — requiring min..max to
        # agree did exactly that. So: once we have a reasonable sample, if a
        # clear majority of runs (>= DECISIVE_AGREEMENT) land on the same
        # side of the threshold as the median, override a spurious
        # "inconclusive" with the arithmetic result.
        if len(scores) < 5:
            return nano
        lo_pct = (min(scores) - baseline_score) / baseline_score * 100.0
        hi_pct = (max(scores) - baseline_score) / baseline_score * 100.0
        median_regressed = expected == "regressed"
        agree = sum(
            1 for s in scores
            if ((s - baseline_score) / baseline_score * 100.0 > threshold_pct) == median_regressed
        )
        decisive = agree / len(scores) >= DECISIVE_AGREEMENT
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


_GIT_SEP = "\x1f"


def list_commit_infos(repo_path: str, start_sha: str, end_sha: str) -> list[CommitInfo]:
    """Metadata for [start] + (start..end], oldest -> newest. Index 0 is the
    known-good start. One `git log` call, so it's cheap even for long ranges."""

    def _git(*args: str) -> str:
        try:
            proc = subprocess.run(["git", *args], cwd=repo_path, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as e:
            raise BisectionError(f"git {' '.join(args)} timed out (30s)") from e
        if proc.returncode != 0:
            raise BisectionError(f"git {' '.join(args)} failed: {proc.stderr}")
        return proc.stdout

    fmt = f"--format=%H{_GIT_SEP}%an{_GIT_SEP}%aI{_GIT_SEP}%s"
    head = _git("log", "-1", fmt, start_sha).strip()
    rest = _git("log", "--reverse", fmt, f"{start_sha}..{end_sha}")
    infos: list[CommitInfo] = []
    for line in [head, *rest.splitlines()]:
        if not line.strip():
            continue
        sha, author, date, subject = (line.split(_GIT_SEP) + ["", "", "", ""])[:4]
        infos.append(CommitInfo(index=len(infos), sha=sha, subject=subject, author=author, date=date))
    return infos


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
    reporter: Reporter | None = None,
    on_probe: Callable[[ProbeRecord], None] | None = None,
    on_plan: Callable[[list[CommitInfo]], None] | None = None,
    on_window: Callable[[int, int], None] | None = None,
    on_skip: Callable[[SkippedCommit], None] | None = None,
    confirm_endpoint: bool = True,
    parallelism: int = 1,
) -> BisectionResult:
    """The real thing: enumerates commits, benchmarks start_sha as the fixed
    baseline, binary-searches the rest via real sandbox runs + real Nano
    calls. Propagates SandboxError / NemotronCallError unwrapped from their
    own modules; raises BisectionError for search-logic-specific failures.

    Live-progress hooks (all optional, all pure observation):
      reporter    — event stream for the terminal (events.py)
      on_probe    — called with a ProbeRecord as each commit finishes
      on_plan     — called once with the commit list before any benchmarking
      on_window   — called with (lo, hi) global indices as the search narrows
      on_skip     — called with a SkippedCommit when a midpoint can't be run and is stepped over

    parallelism: probe up to this many commits at once per round (k-ary search,
    bisect_parallel). 1 = the classic sequential search, unchanged. Only
    meaningful when every probe runs in its own isolated sandbox: on one shared
    machine, concurrent benchmarks disturb each other's timings.

    confirm_endpoint: like `git bisect`, first check the range's *end* really is
    regressed. If it isn't, the honest answer ("no regression in range") costs
    1 probe instead of ~log2(n), and the chart always has both endpoints.
    """
    rep = or_null(reporter)
    commits = list_commits_between(repo_path, start_sha, end_sha)
    infos = list_commit_infos(repo_path, start_sha, end_sha)
    all_shas = [start_sha] + commits
    index_of = {sha: i for i, sha in enumerate(all_shas)}

    log.info("bisector.start", job_id=job_id, start_sha=start_sha, end_sha=end_sha,
              candidate_count=len(commits), regression_threshold_pct=regression_threshold_pct)

    n_total = len(all_shas)
    parallelism = max(1, int(parallelism))
    n_search = max(len(commits) - (1 if confirm_endpoint else 0), 1)
    if parallelism > 1:
        rounds_est = max(1, math.ceil(math.log(n_search + 1, parallelism + 1) - 1e-9))
        max_probes = 1 + (1 if confirm_endpoint else 0) + rounds_est * parallelism
    else:
        rounds_est = max(1, math.ceil(math.log2(max(len(commits), 1) + 1)))
        max_probes = 1 + (1 if confirm_endpoint else 0) + rounds_est
    if on_plan:
        on_plan(infos)
    rep.emit(
        "bisect.plan", "bisect",
        (f"{len(commits)} commits to search — {parallelism} probes at a time, about {rounds_est} round(s) "
         f"instead of {len(commits) + 1} linear runs" if parallelism > 1
         else f"{len(commits)} commits to search — at most ~{max_probes} probes instead of {len(commits) + 1} linear runs"),
        n_commits=n_total, max_probes=max_probes, linear_runs=len(commits) + 1,
        threshold_pct=regression_threshold_pct, n_runs=n_runs, start=start_sha, end=end_sha,
        parallelism=parallelism, rounds=rounds_est,
    )

    probes: list[ProbeRecord] = []
    timeline: list[TimelineEntry] = []
    step_counter = {"n": 0}
    nano_log: dict[str, list[dict[str, Any]]] = {}
    skipped: list[SkippedCommit] = []
    # Parallel probes run in worker threads: the per-probe tag the sandbox
    # listener reads must be per-thread, and the shared lists need a lock.
    _tls = threading.local()
    _lk = threading.Lock()

    def _set_ctx(**kw: Any) -> None:
        _tls.ctx = {**getattr(_tls, "ctx", {}), **kw}

    def _get_ctx() -> dict[str, Any]:
        return dict(getattr(_tls, "ctx", {}))

    # Route sandbox callbacks (checkout / deps / each run) into the stream,
    # tagged with the probe currently in flight.
    if reporter is not None and sandbox.listener is None:
        sandbox.listener = sandbox_listener(rep, _get_ctx)

    def _short(sha: str) -> str:
        return sha[:8]

    # -- baseline -----------------------------------------------------------
    rep.check_cancelled()
    _set_ctx(step=0, index=0, role="baseline")
    span = rep.start("probe", "bisect", f"probe 0 · baseline {_short(start_sha)}",
                     step=0, commit=start_sha, index=0, role="baseline")
    t0 = time.perf_counter()
    baseline_result = sandbox.run_benchmark(start_sha, benchmark_command, n_runs=n_runs)
    baseline_score = statistics.median(baseline_result.raw_scores)
    now = _now_iso()
    timeline.append(TimelineEntry(commit=start_sha, score=baseline_score, timestamp=now))
    base_probe = ProbeRecord(
        step=0, commit=start_sha, index=0, role="baseline", raw_scores=list(baseline_result.raw_scores),
        median_score=baseline_score, pct_change=0.0, verdict="baseline", rounds=0,
        window=(0, n_total - 1), timestamp=now, wall_s=round(time.perf_counter() - t0, 3),
    )
    probes.append(base_probe)
    rep.end(span, f"baseline median {baseline_score:.3f} ms", commit=start_sha, index=0, role="baseline",
            verdict="baseline", median=baseline_score, raw_scores=base_probe.raw_scores, pct_change=0.0,
            threshold_pct=regression_threshold_pct, step=0)
    if on_probe:
        on_probe(base_probe)

    def _run_more(commit_sha: str, n: int) -> list[float]:
        rep.check_cancelled()
        return sandbox.run_benchmark(commit_sha, benchmark_command, n_runs=n).raw_scores

    def _ask_nano(commit_sha: str, scores: list[float]) -> NanoVerdict:
        payload = {
            "commit_range": [start_sha, end_sha],  # overall range — see module docstring
            "candidate_sha": commit_sha,
            "candidate_scores": scores,
            "baseline_score": baseline_score,
            "regression_threshold_pct": regression_threshold_pct,
        }
        extra = {"reporter": reporter} if reporter is not None else {}
        result = call_nemotron(
            job_id=job_id, step=f"bisect:candidate={commit_sha[:10]}",
            model="nano", system_prompt=BISECTOR_SYSTEM_PROMPT,
            payload=payload, temperature=nano_temperature, **extra,
        )
        nano = _validate_nano_verdict(result.parsed, commit_sha=commit_sha)
        final = _reconcile_with_arithmetic(
            nano, scores=scores, baseline_score=baseline_score,
            threshold_pct=regression_threshold_pct, job_id=job_id, commit_sha=commit_sha,
        )
        overridden = final.verdict != nano.verdict
        nano_log.setdefault(commit_sha, []).append({
            "model_id": result.model_id, "latency_s": round(result.latency_s, 3),
            "nano_verdict": nano.verdict, "final_verdict": final.verdict, "overridden": overridden,
            "n_scores": len(scores), "offline": getattr(result, "offline", False),
            "usage": getattr(result, "usage", {}),
        })
        rep.emit(
            "nano.verdict", "nano",
            (f"Nano said '{nano.verdict}' — arithmetic says '{final.verdict}' (override)" if overridden
             else f"Nano verdict: {final.verdict}"),
            commit=commit_sha, nano_verdict=nano.verdict, final_verdict=final.verdict, overridden=overridden,
            median=final.median_score, pct_change=final.pct_change_from_baseline,
            additional_runs=final.additional_runs_needed, n_scores=len(scores),
        )
        return final

    def _evaluate(commit_sha: str, role: Literal["endpoint", "bisect"] = "bisect") -> CandidateEvaluation:
        rep.check_cancelled()
        with _lk:
            step_counter["n"] += 1
            step = step_counter["n"]
        idx = index_of[commit_sha]
        _set_ctx(step=step, index=idx, role=role)
        span = rep.start(
            "probe", "bisect",
            f"probe {step} · {'end of range' if role == 'endpoint' else 'midpoint'} {_short(commit_sha)}",
            step=step, commit=commit_sha, index=idx, role=role,
        )
        t_probe = time.perf_counter()
        try:
            ev = evaluate_candidate(commit_sha, run_more=_run_more, ask_nano=_ask_nano, initial_n_runs=n_runs)
        except (BenchmarkRunError, DependencyInstallError) as e:
            if role != "bisect":
                raise  # the baseline and the range's end must be measurable — fail loudly, as before
            # A midpoint that can't be built or run is stepped over (git bisect skip),
            # not scored and not allowed to kill the search.
            reason = (getattr(e, "detail", "") or str(e)).strip()[-300:]
            nano_log.pop(commit_sha, None)
            rec = SkippedCommit(index=idx, sha=commit_sha, reason=reason, step=step)
            with _lk:
                skipped.append(rec)
            log.warning("bisector.commit_skipped", job_id=job_id, commit=commit_sha, reason=reason[-200:])
            rep.end(
                span, f"skipped — {_short(commit_sha)} can't be run, trying a neighbour",
                commit=commit_sha, index=idx, role=role, verdict="skipped", skipped=True, reason=reason, step=step,
            )
            if on_skip:
                on_skip(rec)
            raise CommitUnrunnable(commit_sha, reason) from e
        now = _now_iso()
        probe = ProbeRecord(
            step=step, commit=commit_sha, index=idx, role=role, raw_scores=list(ev.raw_scores),
            median_score=ev.median_score, pct_change=ev.pct_change_from_baseline, verdict=ev.verdict,
            rounds=ev.rounds, nano=nano_log.pop(commit_sha, []), timestamp=now,
            wall_s=round(time.perf_counter() - t_probe, 3),
        )
        with _lk:
            timeline.append(TimelineEntry(commit=commit_sha, score=ev.median_score, timestamp=now))
            probes.append(probe)
        log.info("bisector.candidate_evaluated", job_id=job_id, commit=commit_sha,
                  verdict=ev.verdict, median_score=ev.median_score,
                  pct_change=ev.pct_change_from_baseline, rounds=ev.rounds)
        rep.end(
            span, f"{ev.verdict} · median {ev.median_score:.3f} ms ({ev.pct_change_from_baseline:+.1f}%)",
            commit=commit_sha, index=idx, role=role, verdict=ev.verdict, median=ev.median_score,
            raw_scores=probe.raw_scores, pct_change=ev.pct_change_from_baseline,
            threshold_pct=regression_threshold_pct, rounds=ev.rounds, step=step,
        )
        if on_probe:
            on_probe(probe)
        return ev

    def _on_step(event: str, lo: int, hi: int, mid: int, ev: CandidateEvaluation | None) -> None:
        # bisect() works on `search` (see below); +1 converts to global indices
        # ([start] + commits), and the end commit (if confirmed) is index n-1.
        g_lo, g_hi = lo + 1, hi + 1
        if event == "skipped":
            rep.emit(
                "bisect.skip", "bisect",
                f"{_short(all_shas[mid + 1])} couldn't be run — skipping it and probing a neighbour instead",
                commit=all_shas[mid + 1], lo=g_lo, hi=g_hi,
            )
        if event == "narrowed":
            if probes:
                probes[-1].window = (g_lo, max(g_hi, g_lo - 1))
            rep.emit(
                "bisect.window", "bisect",
                (f"guilty commit is at or before {_short(all_shas[mid + 1])} — window now "
                 f"{max(0, g_hi - g_lo + 1)} commit(s)") if ev and ev.verdict == "regressed"
                else (f"{_short(all_shas[mid + 1])} is clean — guilty commit is after it — window now "
                      f"{max(0, g_hi - g_lo + 1)} commit(s)"),
                lo=g_lo, hi=g_hi, mid=mid + 1, verdict=ev.verdict if ev else None,
            )
            if on_window:
                on_window(g_lo, max(g_hi, g_lo - 1))

    round_state = {"n": 0}

    def _on_round(event: str, lo: int, hi: int, picked: list[int], results: dict[int, CandidateEvaluation]) -> None:
        """Progress narration for the parallel search (indices are into `search`;
        +1 makes them global, as in _on_step)."""
        g_lo, g_hi = lo + 1, hi + 1
        if event == "start":
            round_state["n"] += 1
            rep.emit(
                "bisect.round", "bisect",
                f"round {round_state['n']}: probing {len(picked)} commit(s) at once — "
                + ", ".join(_short(all_shas[i + 1]) for i in picked),
                round=round_state["n"], commits=[all_shas[i + 1] for i in picked], parallelism=len(picked),
                lo=g_lo, hi=g_hi,
            )
            return
        new_window = (g_lo, max(g_hi, g_lo - 1))
        with _lk:
            for pr in probes:
                if pr.index in {i + 1 for i in picked}:
                    pr.window = new_window
        verdicts = ", ".join(f"{_short(all_shas[i + 1])} {ev.verdict}" for i, ev in sorted(results.items()))
        rep.emit(
            "bisect.window", "bisect",
            f"round {round_state['n']} done ({verdicts or 'nothing runnable'}) — window now "
            f"{max(0, g_hi - g_lo + 1)} commit(s)",
            lo=g_lo, hi=g_hi, round=round_state["n"],
        )
        if on_window:
            on_window(*new_window)

    def _search(sub_commits: list[str]) -> tuple[str | None, list[tuple[int, CandidateEvaluation]]]:
        if parallelism > 1:
            return bisect_parallel(sub_commits, evaluate=_evaluate, parallelism=parallelism,
                                   on_round=_on_round, skipped_out=_skips)
        return bisect(sub_commits, evaluate=_evaluate, on_step=_on_step, skipped_out=_skips)

    t_search = time.perf_counter()

    # -- endpoint confirmation, then binary search ---------------------------
    regression_commit: str | None
    _skips: list[tuple[int, CommitUnrunnable]] = []
    search = commits[:-1] if confirm_endpoint else commits
    if confirm_endpoint:
        end_ev = _evaluate(end_sha, role="endpoint")
        if end_ev.verdict != "regressed":
            regression_commit = None
            rep.emit("bisect.window", "bisect",
                     f"end of range {_short(end_sha)} is not regressed — nothing to bisect",
                     lo=0, hi=-1, mid=n_total - 1, verdict=end_ev.verdict)
            visited: list[tuple[int, CandidateEvaluation]] = []
        else:
            if on_window:
                on_window(1, n_total - 1)
            regression_commit, visited = _search(search)
            if regression_commit is None:
                regression_commit = end_sha  # every earlier commit was clean
    else:
        regression_commit, visited = _search(search)

    # Sort by position in the overall commit order, not "order visited during
    # the search" — the dashboard timeline should read oldest -> newest.
    order = {sha: i for i, sha in enumerate(all_shas)}
    timeline.sort(key=lambda e: order[e.commit])
    probes_sorted_by_step = sorted(probes, key=lambda p: p.step)
    n_evaluated = len(probes) - 1  # excludes the baseline, as before
    search_wall = time.perf_counter() - t_search
    probe_time = sum(p.wall_s for p in probes if p.role != "baseline")

    if regression_commit is not None:
        gi = index_of[regression_commit]
        rep.emit(
            "bisect.done", "bisect",
            f"guilty commit found: {_short(regression_commit)} after {n_evaluated} probes "
            f"(a linear scan needs {len(commits)})"
            + (f"; {len(skipped)} unrunnable commit(s) skipped" if skipped else "")
            + (f" · {round_state['n']} parallel round(s): {probe_time:.0f}s of probe time in {search_wall:.0f}s wall-clock"
               if parallelism > 1 and round_state["n"] else ""),
            regression_commit=regression_commit, index=gi, probes=n_evaluated, linear_runs=len(commits),
            parallelism=parallelism, rounds=round_state["n"], probe_time_s=round(probe_time, 3),
            wall_s=round(search_wall, 3),
            subject=infos[gi].subject if gi < len(infos) else "",
        )
    else:
        rep.emit("bisect.done", "bisect", f"no regression in range — checked with {n_evaluated} probe(s)",
                 regression_commit=None, probes=n_evaluated, linear_runs=len(commits))

    log.info("bisector.result", job_id=job_id, regression_commit=regression_commit,
              candidates_evaluated=n_evaluated, baseline_score=baseline_score)

    return BisectionResult(
        regression_commit=regression_commit, timeline=timeline,
        baseline_score=baseline_score, candidates_evaluated=n_evaluated,
        probes=probes_sorted_by_step, commits=infos, skipped=skipped,
    )
