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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import structlog

from events import Reporter, or_null, sandbox_listener
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
    on_step: Callable[[str, int, int, int, CandidateEvaluation | None], None] | None = None,
) -> tuple[str | None, list[tuple[int, CandidateEvaluation]]]:
    """commits: oldest -> newest, assumed monotonic (clean...clean then
    regressed...regressed — the standard bisection precondition). Binary
    search for the leftmost "regressed" index. Returns
    (regression_commit_or_None, [(index, evaluation), ...] for every commit
    actually evaluated — O(log n), not every commit in the range).

    on_step(event, lo, hi, mid, ev): optional progress hook, called with
    event="probe" just before `mid` is evaluated (ev=None) and event="narrowed"
    right after, with the *new* window (lo, hi). Pure observation — it can't
    influence the search."""
    n = len(commits)
    lo, hi = 0, n - 1
    result: str | None = None
    visited: list[tuple[int, CandidateEvaluation]] = []
    while lo <= hi:
        mid = (lo + hi) // 2
        if on_step:
            on_step("probe", lo, hi, mid, None)
        ev = evaluate(commits[mid])
        visited.append((mid, ev))
        if ev.verdict == "regressed":
            result = commits[mid]
            hi = mid - 1
        else:  # "clean" — evaluate_candidate() already resolved "inconclusive"
            lo = mid + 1
        if on_step:
            on_step("narrowed", lo, hi, mid, ev)
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
    confirm_endpoint: bool = True,
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
    max_probes = 1 + (1 if confirm_endpoint else 0) + max(1, math.ceil(math.log2(max(len(commits), 1) + 1)))
    if on_plan:
        on_plan(infos)
    rep.emit(
        "bisect.plan", "bisect",
        f"{len(commits)} commits to search — at most ~{max_probes} probes instead of {len(commits) + 1} linear runs",
        n_commits=n_total, max_probes=max_probes, linear_runs=len(commits) + 1,
        threshold_pct=regression_threshold_pct, n_runs=n_runs, start=start_sha, end=end_sha,
    )

    probes: list[ProbeRecord] = []
    timeline: list[TimelineEntry] = []
    step_counter = {"n": 0}
    context: dict[str, Any] = {}
    nano_log: dict[str, list[dict[str, Any]]] = {}

    # Route sandbox callbacks (checkout / deps / each run) into the stream,
    # tagged with the probe currently in flight.
    if reporter is not None and sandbox.listener is None:
        sandbox.listener = sandbox_listener(rep, lambda: dict(context))

    def _short(sha: str) -> str:
        return sha[:8]

    # -- baseline -----------------------------------------------------------
    rep.check_cancelled()
    context.update(step=0, index=0, role="baseline")
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
        step_counter["n"] += 1
        step = step_counter["n"]
        idx = index_of[commit_sha]
        context.update(step=step, index=idx, role=role)
        span = rep.start(
            "probe", "bisect",
            f"probe {step} · {'end of range' if role == 'endpoint' else 'midpoint'} {_short(commit_sha)}",
            step=step, commit=commit_sha, index=idx, role=role,
        )
        t_probe = time.perf_counter()
        ev = evaluate_candidate(commit_sha, run_more=_run_more, ask_nano=_ask_nano, initial_n_runs=n_runs)
        now = _now_iso()
        timeline.append(TimelineEntry(commit=commit_sha, score=ev.median_score, timestamp=now))
        probe = ProbeRecord(
            step=step, commit=commit_sha, index=idx, role=role, raw_scores=list(ev.raw_scores),
            median_score=ev.median_score, pct_change=ev.pct_change_from_baseline, verdict=ev.verdict,
            rounds=ev.rounds, nano=nano_log.pop(commit_sha, []), timestamp=now,
            wall_s=round(time.perf_counter() - t_probe, 3),
        )
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

    # -- endpoint confirmation, then binary search ---------------------------
    regression_commit: str | None
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
            regression_commit, visited = bisect(search, evaluate=_evaluate, on_step=_on_step)
            if regression_commit is None:
                regression_commit = end_sha  # every earlier commit was clean
    else:
        regression_commit, visited = bisect(search, evaluate=_evaluate, on_step=_on_step)

    # Sort by position in the overall commit order, not "order visited during
    # the search" — the dashboard timeline should read oldest -> newest.
    order = {sha: i for i, sha in enumerate(all_shas)}
    timeline.sort(key=lambda e: order[e.commit])
    probes_sorted_by_step = sorted(probes, key=lambda p: p.step)
    n_evaluated = len(probes) - 1  # excludes the baseline, as before

    if regression_commit is not None:
        gi = index_of[regression_commit]
        rep.emit(
            "bisect.done", "bisect",
            f"guilty commit found: {_short(regression_commit)} after {n_evaluated} probes "
            f"(a linear scan needs {len(commits)})",
            regression_commit=regression_commit, index=gi, probes=n_evaluated, linear_runs=len(commits),
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
        probes=probes_sorted_by_step, commits=infos,
    )
