# backend/api.py
"""
api.py — job endpoints, the analysis pipeline, and its live event stream.

Endpoints
  POST /analyze                 start a job (commit_range optional → auto-detected)
  POST /demo                    start a job on the bundled sample repo
  GET  /jobs                    history (newest first)
  GET  /jobs/{id}               full job state (+ derived metrics)
  GET  /jobs/{id}/events        live activity stream, cursor-paged (?after=<seq>)
  POST /jobs/{id}/cancel        cooperative cancel
  GET  /jobs/{id}/report.md     PR-ready write-up
  GET  /jobs/{id}/fix.patch     the proposed patch as a real .patch file
  GET  /config  /preflight  /health

Everything is wired for real: sandbox_client.py's get_sandbox(), models.py's
call_nemotron() (via bisector/diagnoser/fixer), bisector.bisect_repo(),
diagnoser.diagnose() and fixer.run_fix_loop_live(). `_run_analysis()` runs the
full pipeline end to end and — new — narrates it: every git call, sandbox run,
Nemotron request/response, citation check and Tavily query is emitted as an
event (events.py) and progress (commits, probes, timeline, fix attempts) is
persisted as it happens, so the dashboard fills in live instead of jumping
from "queued" to "done".

Judgment calls that no doc resolves — flagged, not picked silently
(AGENTS.md: "don't guess silently — flag it explicitly"):

1. What a "done" job with no regression found looks like, and what a
   diagnosis of category "other" does to the pipeline. No regression found ->
   diagnosis/fix/final_result all None (nothing to diagnose). Regression found
   but diagnosed "other" -> diagnosis populated, fix/final_result None (the
   Fixer needs a concrete root cause; inventing a fix against no named cause
   would be exactly the fabrication AGENTS.md rule 1 forbids).

2. AGENTS.md requires failing "with a clear error," but the documented schema
   had nowhere to put one. Added optional `error: str | None` (status="failed").

3. `commit_range` is optional. RESOLVED (this used to be a hard 400): omit it
   and the last AUTO_RANGE_COMMITS (default 30) first-parent commits up to HEAD
   are searched, clamped to the repo's history. Either end may be any git rev
   (sha, tag, branch). Both ends are resolved and validated inside the job and
   surface as a clear failure event — the request itself only validates shape.

4. `repo_url` may be a real git remote (cloned once per job) or a local path.
   HARDENING: remotes must be https/http/ssh (git's `ext::`/`file:` transports
   are refused and GIT_ALLOW_PROTOCOL is set as a second line of defence);
   revs/URLs beginning with "-" are rejected (git option injection); clones
   never block on a credential prompt. Local paths are accepted only when the
   local sandbox is the backend, or CULPRIT_ALLOW_LOCAL_REPOS=1.

5. The guilty commit's diff/message/file contents come from plain `git`
   subprocess calls here (diagnoser/fixer stay Nemotron-only). Very large
   diffs/files are capped with an explicit truncation marker so one lockfile
   can't blow the model's context; the same capped diff is what citations are
   verified against, so verification stays consistent.

6. "Resolved" = back within `regression_threshold_pct` of the original
   baseline, computed in Python from real sandbox numbers — the same yardstick
   the Bisector used to call the commit "regressed".

7. AGENT_SPECS.md §3 types `diagnosis` as "output of Diagnoser agent" (a plain
   dict) — `dataclasses.asdict(diagnosis_result)` is passed through unchanged.

8. Concurrency: at most MAX_CONCURRENT_JOBS (default 2) jobs run at once; the
   rest genuinely wait in "queued". Bounded so a demo audience can't fan out
   unbounded sandbox spend.

9. Cancellation is cooperative: checked between probes, between run rounds and
   between fix attempts — never mid-sandbox-run — and yields status
   "cancelled", which is neither done nor failed.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import structlog
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from dotenv import load_dotenv

# Load backend/.env BEFORE importing modules that read os.environ at import time
# (models.py reads the Nemotron model IDs at import). Real shell env vars still
# win over .env, so a one-off `export` keeps working as an override.
load_dotenv(Path(__file__).with_name(".env"))

from bisector import ProbeRecord, bisect_repo
from demo_repo import BENCHMARK_COMMAND as DEMO_BENCHMARK_COMMAND, build_demo_repo
from diagnoser import DiagnosisResult, diagnose
from events import JobCancelled, Reporter, now_iso, sandbox_listener, summarize
from fixer import FixLoopResult, FixProposal, VerifyOutcome, run_fix_loop_live
from preflight import run_preflight
from report import render_report
from sandbox_client import BenchmarkRunError, SandboxError, get_sandbox
from schema import (  # noqa: F401 — re-exported: `from api import InMemoryJobStore, JobState, ...`
    TERMINAL_STATUSES,
    CommitModel,
    DiagnosisModel,
    EventStore,
    FixAttemptModel,
    FixModel,
    InMemoryEventStore,
    InMemoryJobStore,
    JobNotFoundError,
    JobState,
    JobStore,
    JobSummary,
    ProbeModel,
    SqliteEventStore,
    SqliteJobStore,
    TavilyRefModel,
    TimelineEntryModel,
    summarize_job,
)

log = structlog.get_logger("api")

_DEFAULT_REGRESSION_THRESHOLD_PCT = float(os.environ.get("REGRESSION_THRESHOLD_PCT", "15.0"))
_DEFAULT_N_RUNS = int(os.environ.get("BENCHMARK_N_RUNS", "5"))
_USE_TOKEN_FACTORY = os.environ.get("USE_TOKEN_FACTORY") == "1"
_AUTO_RANGE_COMMITS = int(os.environ.get("AUTO_RANGE_COMMITS", "30"))
_MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))
_MAX_DIFF_CHARS = int(os.environ.get("CULPRIT_MAX_DIFF_CHARS", "150000"))
_MAX_CONTEXT_CHARS = int(os.environ.get("CULPRIT_MAX_CONTEXT_CHARS", "300000"))
_MAX_FILE_CHARS = 120_000

# CORS — the dashboard calls this API directly from the browser, from a
# different origin. Env-configurable (comma-separated); credentials are never
# sent, so a wildcard for local/demo use doesn't expose cookies/auth.
_CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_offline() -> bool:
    return os.environ.get("CULPRIT_OFFLINE") == "1"


def _mark_stage(job: JobState, status: str) -> None:
    """Set job.status and record when that stage began."""
    job.status = status  # type: ignore[assignment]
    job.stage_times.setdefault(status, _now_iso())


def _new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def _data_dir() -> Path:
    return Path(os.environ.get("CULPRIT_DATA_DIR") or Path(__file__).with_name(".data"))


# ---------------------------------------------------------------------------
# Request models (with input hardening — see docstring flag #4)
# ---------------------------------------------------------------------------

_REV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@^~{}\-]{0,199}$")
_REMOTE_RE = re.compile(r"^(https?://|ssh://|git@[\w.\-]+:)\S+$")


class AnalyzeRequest(BaseModel):
    repo_url: str = Field(min_length=1, max_length=2048)
    benchmark_command: str = Field(min_length=1, max_length=2000)
    commit_range: list[str] | None = None  # [start_rev, end_rev]; omit to auto-detect
    label: str | None = Field(default=None, max_length=120)

    @field_validator("repo_url", "benchmark_command")
    @classmethod
    def _strip_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("repo_url")
    @classmethod
    def _no_option_injection(cls, v: str) -> str:
        if v.startswith("-"):
            raise ValueError("repo_url must not start with '-'")
        return v


class AnalyzeResponse(BaseModel):
    job_id: str


def _diagnosis_to_model(d: DiagnosisResult) -> DiagnosisModel:
    """Adapter between diagnoser.DiagnosisResult and the wire schema."""
    return DiagnosisModel(
        category=d.category,
        explanation=d.explanation,
        cited_lines=d.cited_lines,
        confidence=d.confidence,
        tavily_refs=[TavilyRefModel(title=r.title, url=r.url, snippet=r.snippet, score=r.score) for r in d.tavily_refs],
        citation_verification_failed=d.citation_verification_failed,
        diff=d.diff or None,
    )


# ---------------------------------------------------------------------------
# Repo resolution (see flag #4)
# ---------------------------------------------------------------------------

def _resolve_repo_path(repo_url: str, workdir: Path, *, allow_local: bool = True, reporter: Reporter | None = None) -> Path:
    local = Path(repo_url)
    if local.exists() and (local / ".git").exists():
        if not allow_local:
            raise ValueError(
                "local repository paths are disabled on this server (they are only allowed with the local "
                "sandbox or CULPRIT_ALLOW_LOCAL_REPOS=1) — use an https:// or ssh git URL"
            )
        return local.resolve()
    if not _REMOTE_RE.match(repo_url):
        raise ValueError(
            f"{repo_url!r} is not a git repository on this machine and not a supported remote URL "
            "(use https://…, ssh://… or git@host:…)"
        )
    dest = workdir / "repo"
    rep = reporter
    span = rep.start("repo", "git", f"cloning {repo_url}", url=repo_url) if rep else ""
    try:
        proc = subprocess.run(
            ["git", "clone", "-q", "--", repo_url, str(dest)],
            capture_output=True, text=True, timeout=300,
            # Second line of defence against git's exotic transports, and never block on a credential prompt.
            env={**os.environ, "GIT_ALLOW_PROTOCOL": "https:http:ssh", "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired as e:
        if rep:
            rep.end(span, "clone timed out", ok=False)
        raise ValueError(f"git clone of {repo_url} timed out (300s)") from e
    if proc.returncode != 0:
        if rep:
            rep.end(span, "clone failed", ok=False, error=proc.stderr[-400:])
        raise ValueError(f"failed to clone {repo_url}: {proc.stderr}")
    if rep:
        rep.end(span, "clone complete", ok=True)
    return dest


def _run_git(args: list[str], *, cwd: Path, timeout: int = 30, reporter: Reporter | None = None) -> str:
    """Runs a git command, raising ValueError on a non-zero exit or a timeout —
    never lets a raw subprocess.TimeoutExpired escape uncaught."""
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ValueError(f"git {' '.join(args)} timed out ({timeout}s)") from e
    if reporter is not None:
        reporter.emit("git.call", "git", "$ git " + " ".join(a if len(a) < 60 else a[:12] + "…" for a in args),
                      cmd=["git", *args], dur_s=round(time.perf_counter() - t0, 3), ok=proc.returncode == 0)
    if proc.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


def _resolve_range(
    repo_path: Path, commit_range: list[str] | None, auto_n: int, reporter: Reporter,
) -> tuple[str, str]:
    """Resolve the user's revs (or an auto-detected range) to full SHAs and
    validate them. See docstring flag #3."""

    def _commit(rev: str) -> str:
        if not _REV_RE.match(rev):
            raise ValueError(f"{rev!r} is not a valid revision")
        try:
            return _run_git(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], cwd=repo_path, reporter=reporter).strip()
        except ValueError:
            raise ValueError(f"{rev!r} is not a commit in this repository") from None

    if commit_range:
        start, end = _commit(commit_range[0]), _commit(commit_range[1])
        auto = False
    else:
        end = _commit("HEAD")
        total = int(_run_git(["rev-list", "--count", "HEAD"], cwd=repo_path, reporter=reporter).strip() or "0")
        if total < 2:
            raise ValueError("the repository needs at least 2 commits to search for a regression")
        n = min(auto_n, total - 1)
        chain = _run_git(
            ["rev-list", "--first-parent", f"--max-count={n + 1}", "HEAD"], cwd=repo_path, reporter=reporter,
        ).split()
        start = chain[-1]
        auto = True

    if start == end:
        raise ValueError("known-good and known-bad are the same commit — nothing to search")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", start, end], cwd=repo_path, capture_output=True, text=True, timeout=30,
    )
    if ancestor.returncode == 1:
        raise ValueError(f"known-good {start[:10]} is not an ancestor of known-bad {end[:10]} — swap them or pick a linear range")
    if ancestor.returncode not in (0, 1):
        raise ValueError(f"could not validate the commit range: {ancestor.stderr.strip()}")
    reporter.emit(
        "git.range", "git",
        (f"auto-detected range: last {n} commits " if auto else "commit range: ") + f"{start[:10]}..{end[:10]}",
        start=start, end=end, auto=auto,
    )
    return start, end


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated {len(text) - limit} chars]"


def _commit_diff_and_message(
    repo_path: Path, commit_sha: str, reporter: Reporter | None = None,
) -> tuple[str, str, list[str]]:
    """(diff, commit_message, changed_file_paths) for commit_sha's own change —
    the diff against its immediate parent."""
    parent = f"{commit_sha}^"
    diff = _run_git(["diff", parent, commit_sha], cwd=repo_path, reporter=reporter)
    message = _run_git(["log", "-1", "--format=%B", commit_sha], cwd=repo_path, reporter=reporter).strip()
    changed = [
        line for line in _run_git(["diff", "--name-only", parent, commit_sha], cwd=repo_path, reporter=reporter).splitlines()
        if line.strip()
    ]
    return _cap(diff, _MAX_DIFF_CHARS), message, changed


def _concat_file_contents_at_commit(repo_path: Path, commit_sha: str, paths: list[str]) -> str:
    """Per fixer.py's calling-convention note: concatenate with clear file-path
    markers. A path git can't show as text at this commit (deleted, binary) is
    noted rather than failing the whole job over one file."""
    parts = []
    budget = _MAX_CONTEXT_CHARS
    for path in paths:
        try:
            contents = _run_git(["show", f"{commit_sha}:{path}"], cwd=repo_path)
        except ValueError:
            parts.append(f"# FILE: {path}\n<unavailable at {commit_sha[:10]} — deleted or not text>")
            continue
        contents = _cap(contents, min(_MAX_FILE_CHARS, max(budget, 0)))
        budget -= len(contents)
        parts.append(f"# FILE: {path}\n{contents}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Runtime: shared state for running jobs
# ---------------------------------------------------------------------------

class _Runtime:
    """Everything a running job needs from the app: stores, a lock that makes
    read-modify-write of a job atomic (the pipeline and the cancel endpoint both
    write), cancel flags, and the concurrency limiter."""

    def __init__(self, store: JobStore, events: EventStore, max_concurrent: int) -> None:
        self.store = store
        self.events = events
        self.lock = threading.RLock()
        self._cancel: dict[str, threading.Event] = {}
        self.slots = threading.BoundedSemaphore(max(1, max_concurrent))

    def cancel_flag(self, job_id: str) -> threading.Event:
        with self.lock:
            return self._cancel.setdefault(job_id, threading.Event())

    def update(self, job_id: str, mutate: Callable[[JobState], None]) -> JobState:
        with self.lock:
            job = self.store.get(job_id)
            mutate(job)
            job.updated_at = _now_iso()
            self.store.save(job)
            return job

    def reporter(self, job_id: str) -> Reporter:
        start = self.events.last_seq(job_id)
        return Reporter(job_id, sink=lambda e: self.events.append(job_id, e), cancel=self.cancel_flag(job_id), start_seq=start)


# ---------------------------------------------------------------------------
# The pipeline itself
# ---------------------------------------------------------------------------

def _run_analysis(
    *,
    rt: _Runtime,
    job_id: str,
    repo_url: str,
    benchmark_command: str,
    commit_range: list[str] | None,
    regression_threshold_pct: float,
    n_runs: int,
    offline: bool,
    use_token_factory: bool,
    allow_local: bool,
    auto_range_commits: int = _AUTO_RANGE_COMMITS,
    prebuilt_repo: Path | None = None,
) -> None:
    reporter = rt.reporter(job_id)
    workdir = Path(tempfile.mkdtemp(prefix=f"prd_job_{job_id}_"))
    slot_held = False
    try:
        reporter.emit(
            "job.start", "system", f"job {job_id} accepted", job_id=job_id, repo=repo_url,
            benchmark=benchmark_command, threshold_pct=regression_threshold_pct, n_runs=n_runs,
            mode="offline" if offline else "live", sandbox="token_factory" if use_token_factory else "local",
        )
        if not rt.slots.acquire(blocking=False):
            reporter.emit("job.waiting", "system", "all worker slots are busy — waiting in the queue")
            while not rt.slots.acquire(timeout=0.5):
                reporter.check_cancelled()
        slot_held = True
        reporter.check_cancelled()

        def _begin(j: JobState) -> None:
            _mark_stage(j, "bisecting")
            j.threshold_pct = regression_threshold_pct
            j.n_runs = n_runs

        rt.update(job_id, _begin)
        reporter.emit("stage.start", "system", "stage 1/3 · bisect", stage="bisecting")

        repo_path = prebuilt_repo or _resolve_repo_path(repo_url, workdir, allow_local=allow_local, reporter=reporter)
        start_sha, end_sha = _resolve_range(repo_path, commit_range, auto_range_commits, reporter)
        rt.update(job_id, lambda j: setattr(j, "commit_range", [start_sha, end_sha]))

        sandbox = get_sandbox(str(repo_path), use_token_factory=use_token_factory)
        reporter.emit(
            "sandbox.ready", "sandbox",
            "Token Factory Sandboxes — every commit runs in a fresh, disposable VM" if use_token_factory
            else "local sandbox — every commit runs in a fresh git worktree with a lockfile-cached venv",
            backend="token_factory" if use_token_factory else "local",
        )

        # ---- progressive persistence hooks --------------------------------
        def _on_plan(infos) -> None:
            rt.update(job_id, lambda j: setattr(j, "commits", [
                CommitModel(index=c.index, sha=c.sha, subject=c.subject, author=c.author, date=c.date) for c in infos
            ]))

        def _on_probe(p: ProbeRecord) -> None:
            def m(j: JobState) -> None:
                probe = ProbeModel(
                    step=p.step, commit=p.commit, index=p.index, role=p.role, raw_scores=p.raw_scores,
                    median_score=p.median_score, pct_change=p.pct_change, verdict=p.verdict, rounds=p.rounds,
                    nano=p.nano, window=list(p.window) if p.window else None, timestamp=p.timestamp, wall_s=p.wall_s,
                )
                j.probes = [x for x in j.probes if x.step != p.step] + [probe]
                if p.step == 0:
                    j.baseline_score = p.median_score
                # Timeline reads oldest → newest (by position in the range), not probe order.
                j.timeline = [
                    TimelineEntryModel(commit=x.commit, score=x.median_score, timestamp=x.timestamp)
                    for x in sorted(j.probes, key=lambda x: x.index)
                ]

            rt.update(job_id, m)

        def _on_window(lo: int, hi: int) -> None:
            rt.update(job_id, lambda j: setattr(j, "window", [lo, hi]))

        result = bisect_repo(
            job_id=job_id, repo_path=str(repo_path), start_sha=start_sha, end_sha=end_sha,
            benchmark_command=benchmark_command, sandbox=sandbox,
            regression_threshold_pct=regression_threshold_pct, n_runs=n_runs,
            reporter=reporter, on_probe=_on_probe, on_plan=_on_plan, on_window=_on_window,
        )

        def _bisect_done(j: JobState) -> None:
            j.timeline = [TimelineEntryModel(commit=e.commit, score=e.score, timestamp=e.timestamp) for e in result.timeline]
            j.regression_commit = result.regression_commit
            j.baseline_score = result.baseline_score

        job = rt.update(job_id, _bisect_done)

        if result.regression_commit is None:
            # Nothing to diagnose — an honest "clean" result (flag #1).
            rt.update(job_id, lambda j: _mark_stage(j, "done"))
            reporter.emit("job.done", "system", "done — no regression in range", outcome="no_regression")
            log.info("api.job_done", job_id=job_id, regression_commit=None,
                     candidates_evaluated=result.candidates_evaluated)
            return

        reporter.check_cancelled()
        rt.update(job_id, lambda j: _mark_stage(j, "diagnosing"))
        reporter.emit("stage.start", "system", "stage 2/3 · diagnose", stage="diagnosing")

        diff, commit_message, changed_paths = _commit_diff_and_message(repo_path, result.regression_commit, reporter)
        full_file_contents = _concat_file_contents_at_commit(repo_path, result.regression_commit, changed_paths)
        after_score = next(e.score for e in result.timeline if e.commit == result.regression_commit)

        diagnosis_result = diagnose(
            job_id=job_id, guilty_commit_sha=result.regression_commit, diff=diff,
            surrounding_context=full_file_contents, commit_message=commit_message,
            before_score=result.baseline_score, after_score=after_score, reporter=reporter,
        )
        rt.update(job_id, lambda j: setattr(j, "diagnosis", _diagnosis_to_model(diagnosis_result)))

        if diagnosis_result.category == "other":
            # An honest "other" diagnosis has no concrete, cited root cause for
            # the Fixer to act on — flag #1. final_result stays None.
            rt.update(job_id, lambda j: _mark_stage(j, "done"))
            reporter.emit("job.done", "system", "done — diagnosed as 'other'; no concrete cause to fix",
                          outcome="diagnosed_other")
            log.info("api.job_done", job_id=job_id, regression_commit=result.regression_commit,
                     candidates_evaluated=result.candidates_evaluated, diagnosis_category="other")
            return

        reporter.check_cancelled()
        rt.update(job_id, lambda j: _mark_stage(j, "fixing"))
        reporter.emit("stage.start", "system", "stage 3/3 · fix & verify", stage="fixing")

        # Re-point the sandbox's live narration at the verify phase.
        verify_ctx: dict[str, Any] = {"phase": "verify", "attempt": 0}
        sandbox.listener = sandbox_listener(reporter, lambda: dict(verify_ctx))

        regressed_score = next(
            (t.score for t in result.timeline if t.commit == result.regression_commit), result.baseline_score,
        )

        def _verify(proposal: FixProposal) -> VerifyOutcome:
            verify_ctx["attempt"] += 1
            try:
                bench_result = sandbox.apply_patch_and_benchmark(
                    result.regression_commit, proposal.patch, benchmark_command, n_runs=n_runs,
                )
            except BenchmarkRunError as e:  # must precede `except SandboxError` — it's a subclass
                # The sandbox worked; the *patched code* crashed / timed out /
                # printed no number. LLM patches do that — it's a failed
                # attempt with feedback, not a dead job (infra failures above
                # still fail the job).
                log.warning("fixer.patched_run_failed", job_id=job_id, error=str(e)[-300:])
                reporter.emit("verify.crashed", "sandbox",
                              "the patched code did not run cleanly — counting it as a failed attempt and feeding the error back",
                              attempt=verify_ctx["attempt"], error=e.detail or str(e)[-600:])
                return VerifyOutcome(score_after=regressed_score, resolved=False,
                                     note=f"the patched benchmark failed to run: {e.detail or str(e)[-600:]}")
            except SandboxError as e:
                if "patch did not apply" not in str(e):
                    raise  # a real sandbox failure still fails the job
                # Model-written diffs are often malformed. That's a failed
                # attempt (the loop retries), not a dead job.
                log.warning("fixer.patch_did_not_apply", job_id=job_id, error=str(e)[-300:])
                reporter.emit("patch.rejected", "sandbox", "patch did not apply cleanly — counting it as a failed attempt",
                              attempt=verify_ctx["attempt"], error=str(e)[-400:])
                return VerifyOutcome(score_after=regressed_score, resolved=False,
                                     note=f"the patch did not apply cleanly: {str(e)[-600:]}")
            score_after = statistics.median(bench_result.raw_scores)
            pct_change = (
                ((score_after - result.baseline_score) / result.baseline_score) * 100
                if result.baseline_score else 0.0
            )
            resolved = pct_change <= regression_threshold_pct  # flag #6
            reporter.emit(
                "verify.result", "sandbox",
                f"patched median {score_after:.3f} ms is {pct_change:+.1f}% vs baseline — "
                + ("within threshold ✔" if resolved else f"still above the {regression_threshold_pct:g}% threshold"),
                attempt=verify_ctx["attempt"], median=score_after, raw_scores=bench_result.raw_scores,
                pct_change=pct_change, threshold_pct=regression_threshold_pct, resolved=resolved,
            )
            return VerifyOutcome(score_after=score_after, resolved=resolved)

        def _on_attempt(a) -> None:
            def m(j: JobState) -> None:
                j.fix_attempts = [x for x in j.fix_attempts if x.attempt != a.attempt] + [
                    FixAttemptModel(attempt=a.attempt, patch=a.patch, rationale=a.rationale,
                                    score_after=a.score_after, resolved=a.resolved, error=a.error or None)
                ]
                # Show the running fix summary live; `verified` only ever
                # mirrors a real sandbox verdict (rule 1).
                j.fix = FixModel(patch_diff=a.patch, verified=a.resolved,
                                 before_score=result.baseline_score, after_score=a.score_after)

            rt.update(job_id, m)

        fix_loop_result: FixLoopResult = run_fix_loop_live(
            job_id=job_id,
            diagnosis=dataclasses.asdict(diagnosis_result),  # flag #7
            full_file_contents=full_file_contents,
            verify=_verify,
            before_score=result.baseline_score,
            on_attempt=_on_attempt,
            reporter=reporter,
        )

        def _finish(j: JobState) -> None:
            j.fix_attempts = [
                FixAttemptModel(attempt=a.attempt, patch=a.patch, rationale=a.rationale,
                                score_after=a.score_after, resolved=a.resolved, error=a.error or None)
                for a in fix_loop_result.attempts
            ]
            if fix_loop_result.final_patch is not None:
                j.fix = FixModel(
                    patch_diff=fix_loop_result.final_patch,
                    verified=fix_loop_result.resolved,
                    before_score=fix_loop_result.before_score,
                    after_score=(
                        fix_loop_result.after_score
                        if fix_loop_result.after_score is not None else fix_loop_result.before_score
                    ),
                )
            j.final_result = "resolved" if fix_loop_result.resolved else "unresolved_diagnosis_only"
            _mark_stage(j, "done")

        job = rt.update(job_id, _finish)
        reporter.emit(
            "job.done", "system",
            "done — fix verified in a sandbox re-run" if fix_loop_result.resolved
            else "done — diagnosed, but no fix reached the threshold (reported honestly)",
            outcome=job.final_result,
        )
        log.info("api.job_done", job_id=job_id, regression_commit=result.regression_commit,
                 candidates_evaluated=result.candidates_evaluated, final_result=job.final_result)
    except JobCancelled:
        rt.update(job_id, lambda j: _mark_stage(j, "cancelled"))
        reporter.emit("job.cancelled", "system", "cancelled by user")
        log.info("api.job_cancelled", job_id=job_id)
    except Exception as e:  # noqa: BLE001 — any failure becomes an honest failed job
        err = f"{type(e).__name__}: {e}"

        def _fail(j: JobState) -> None:
            _mark_stage(j, "failed")
            j.error = err

        rt.update(job_id, _fail)
        reporter.emit("job.failed", "system", err, error=err)
        log.error("api.job_failed", job_id=job_id, error=err)
    finally:
        if slot_held:
            rt.slots.release()
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# FastAPI app — factory so tests get an isolated store, not a shared global
# ---------------------------------------------------------------------------

def _default_stores(data_dir: Path) -> tuple[JobStore, EventStore]:
    if os.environ.get("CULPRIT_DB", "").lower() == "memory":
        return InMemoryJobStore(), InMemoryEventStore()
    sqlite_store = SqliteJobStore(os.environ.get("CULPRIT_DB") or data_dir / "culprit.db")
    return sqlite_store, SqliteEventStore(sqlite_store)


def create_app(
    store: JobStore | None = None,
    event_store: EventStore | None = None,
    *,
    data_dir: Path | None = None,
) -> FastAPI:
    data_dir = data_dir or _data_dir()
    if store is None:
        store, default_events = _default_stores(data_dir)
        event_store = event_store or default_events
    event_store = event_store or InMemoryEventStore()

    recovered = store.mark_orphans_failed("The server restarted while this job was running, so it could not finish.")
    if recovered:
        log.warning("api.recovered_orphans", count=recovered)

    rt = _Runtime(store, event_store, _MAX_CONCURRENT_JOBS)
    app = FastAPI(title="Culprit — Performance Regression Detective")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    def _allow_local() -> bool:
        return (not _USE_TOKEN_FACTORY) or _is_offline() or os.environ.get("CULPRIT_ALLOW_LOCAL_REPOS") == "1"

    def _start_job(
        background_tasks: BackgroundTasks, *, repo_url: str, benchmark_command: str,
        commit_range: list[str] | None, label: str | None, prebuilt_repo: Path | None = None,
    ) -> str:
        offline = _is_offline()
        job_id = _new_job_id()
        now = _now_iso()
        job = JobState(
            job_id=job_id, status="queued", repo_url=repo_url, benchmark_command=benchmark_command,
            commit_range=commit_range, created_at=now, updated_at=now,
            mode="offline" if offline else "live", label=label,
            threshold_pct=_DEFAULT_REGRESSION_THRESHOLD_PCT, n_runs=_DEFAULT_N_RUNS,
        )
        store.create(job)
        background_tasks.add_task(
            _run_analysis, rt=rt, job_id=job_id, repo_url=repo_url, benchmark_command=benchmark_command,
            commit_range=commit_range, regression_threshold_pct=_DEFAULT_REGRESSION_THRESHOLD_PCT,
            n_runs=_DEFAULT_N_RUNS, offline=offline, use_token_factory=_USE_TOKEN_FACTORY and not offline,
            allow_local=_allow_local() or prebuilt_repo is not None, prebuilt_repo=prebuilt_repo,
        )
        return job_id

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/config")
    def config() -> dict[str, Any]:
        offline = _is_offline()
        return {
            "mode": "offline" if offline else "live",
            "sandbox_backend": "token_factory" if (_USE_TOKEN_FACTORY and not offline) else "local",
            "threshold_pct": _DEFAULT_REGRESSION_THRESHOLD_PCT,
            "n_runs": _DEFAULT_N_RUNS,
            "auto_range_commits": _AUTO_RANGE_COMMITS,
            "max_concurrent_jobs": _MAX_CONCURRENT_JOBS,
            "demo_benchmark_command": DEMO_BENCHMARK_COMMAND,
        }

    @app.get("/preflight")
    def preflight() -> dict[str, Any]:
        return run_preflight()

    @app.post("/analyze", response_model=AnalyzeResponse)
    def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks) -> AnalyzeResponse:
        commit_range = req.commit_range
        if commit_range is not None:
            if len(commit_range) == 0:
                commit_range = None
            elif len(commit_range) != 2:
                raise HTTPException(400, "commit_range must be exactly [start_rev, end_rev] (or omitted to auto-detect)")
            else:
                commit_range = [r.strip() for r in commit_range]
                for rev in commit_range:
                    if not _REV_RE.match(rev):
                        raise HTTPException(400, f"{rev!r} is not a valid git revision (sha, tag or branch name)")
        job_id = _start_job(
            background_tasks, repo_url=req.repo_url, benchmark_command=req.benchmark_command,
            commit_range=commit_range, label=req.label,
        )
        return AnalyzeResponse(job_id=job_id)

    @app.post("/demo", response_model=AnalyzeResponse)
    def demo(background_tasks: BackgroundTasks) -> AnalyzeResponse:
        try:
            repo = build_demo_repo(data_dir / "demo" / "orders-service")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"could not build the bundled demo repo: {e}") from e
        job_id = _start_job(
            background_tasks, repo_url=repo.path, benchmark_command=repo.benchmark_command,
            commit_range=[repo.start_sha, repo.end_sha], label="Bundled demo · orders-service",
            prebuilt_repo=Path(repo.path),
        )
        return AnalyzeResponse(job_id=job_id)

    @app.get("/jobs", response_model=list[JobSummary])
    def list_jobs(limit: int = Query(50, ge=1, le=200)) -> list[JobSummary]:
        return [summarize_job(j) for j in store.list(limit)]

    def _load(job_id: str) -> JobState:
        try:
            return store.get(job_id)
        except JobNotFoundError:
            raise HTTPException(404, f"job {job_id} not found") from None

    def _metrics(job_id: str) -> dict[str, Any]:
        return summarize(event_store.of_kinds(job_id, ("model.end", "sandbox.run", "probe.end", "tavily.end", "sandbox.instance.end", "git.call")))

    @app.get("/jobs/{job_id}", response_model=JobState)
    def get_job(job_id: str) -> JobState:
        job = _load(job_id)
        return job.model_copy(update={"server_time": _now_iso(), "metrics": _metrics(job_id)})

    @app.get("/jobs/{job_id}/events")
    def get_events(job_id: str, after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000)) -> dict[str, Any]:
        job = _load(job_id)
        evs = event_store.since(job_id, after, limit)
        next_seq = evs[-1].seq if evs else after
        # "done" only once the job is terminal AND the cursor has caught up, so a
        # client never stops polling with events still unread.
        drained = job.status in TERMINAL_STATUSES and event_store.last_seq(job_id) <= next_seq
        return {"events": [e.to_dict() for e in evs], "next": next_seq, "done": drained, "status": job.status}

    @app.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> dict[str, Any]:
        job = _load(job_id)
        if job.status in TERMINAL_STATUSES:
            raise HTTPException(409, f"job is already {job.status}")
        rt.cancel_flag(job_id).set()
        rt.update(job_id, lambda j: setattr(j, "cancel_requested", True))
        rt.reporter(job_id).emit("job.cancel_requested", "system", "cancel requested — stopping at the next checkpoint")
        return {"ok": True, "status": job.status}

    @app.get("/jobs/{job_id}/report.md", response_class=PlainTextResponse)
    def report(job_id: str) -> PlainTextResponse:
        job = _load(job_id)
        return PlainTextResponse(
            render_report(job, _metrics(job_id)), media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="culprit-{job_id}.md"'},
        )

    @app.get("/jobs/{job_id}/fix.patch", response_class=PlainTextResponse)
    def fix_patch(job_id: str) -> PlainTextResponse:
        job = _load(job_id)
        if job.fix is None:
            raise HTTPException(404, "this job has no proposed fix")
        return PlainTextResponse(
            job.fix.patch_diff if job.fix.patch_diff.endswith("\n") else job.fix.patch_diff + "\n",
            media_type="text/x-diff; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="culprit-{job_id}.patch"'},
        )

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
