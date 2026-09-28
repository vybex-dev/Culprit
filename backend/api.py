# backend/api.py
"""
api.py — job endpoints (POST /analyze, GET /jobs/{job_id}), per BUILD_01 §1
and the reconciled schema in BUILD_00_OVERVIEW.md.

Everything is wired up for real here: sandbox_client.py's get_sandbox(),
models.py's call_nemotron() (via bisector.py/diagnoser.py/fixer.py),
bisector.py's bisect_repo(), diagnoser.py's diagnose(), and fixer.py's
run_fix_loop_live(). `_run_analysis()` below now runs the full pipeline
BUILD_01/BUILD_02 describe end to end: repo in -> guilty commit + timeline
-> root-cause diagnosis (+ Tavily grounding) -> patch proposal/verify loop
-> fix (or an honest "diagnosed, not auto-fixed") out. (As of
CODE_REVIEW_FINDINGS.md #3: this used to stop at the guilty commit +
timeline, with Diagnoser/Fixer built but never called — that gap is what
this wiring closes.)

Judgment calls made here that no doc resolves, flagged rather than picked
silently (AGENTS.md: "don't guess silently — flag it explicitly"):

1. What a "done" job with no regression found looks like, and what a
   diagnosis of category "other" does to the pipeline. The documented
   schema already makes `diagnosis`, `fix`, and `final_result`
   optional/nullable, so both of these go straight to status="done" with
   whatever downstream fields genuinely don't apply left None: no
   regression found -> diagnosis/fix/final_result all None (nothing to
   diagnose); regression found but diagnosed as "other" -> diagnosis is
   populated, but fix/final_result stay None (Fixer needs a concrete root
   cause to act on — AGENT_SPECS.md doesn't say what it should do with
   "other", and inventing a fix attempt against no named cause would be
   exactly the kind of fabrication AGENTS.md rule 1 forbids). Neither case
   is a fabricated "fully resolved" result — a dashboard reading either
   state sees exactly what happened, nothing claimed beyond that.

2. AGENTS.md requires failing "with a clear error," but the documented
   schema has nowhere to put one. Added one optional field, `error: str |
   None`, used only when status="failed". This is the one place this
   module's job-state shape differs from BUILD_00_OVERVIEW.md's — everything
   else matches exactly (plus the additive `DiagnosisModel` fields flagged
   in the schema section below, per CODE_REVIEW_FINDINGS.md #8/#9).

3. `POST /analyze`'s `commit_range` is documented as optional, but nothing
   says what "auto-detect a range" should mean (last N commits? earliest
   tag to HEAD? something else?) — real design decisions I didn't want to
   guess at. For now, omitting it is a 400, not a silent guess. (The
   frontend's new-analysis form now marks both fields required for the
   same reason — see frontend/src/app/page.tsx — rather than inviting an
   input this endpoint always rejects; CODE_REVIEW_FINDINGS.md #2.)

4. `repo_url` needs to work for both a real git remote (clone it once, per
   job, into a scratch dir) and a local path (useful for testing without
   network access) — TRD/AGENT_SPECS don't say, so `_resolve_repo_path()`
   below just accepts either.

5. Neither doc says how api.py should obtain the guilty commit's diff,
   commit message, and file contents to hand to diagnoser.py/fixer.py —
   diagnoser.py/fixer.py both just accept them as plain string parameters
   and stay Nemotron-only (BUILD_02 §"What you own"). `_commit_diff_and_message()`
   / `_concat_file_contents_at_commit()` below do this with plain `git`
   subprocess calls, the same pattern bisector.py/sandbox_client.py already
   use elsewhere in this file's call graph.

6. Neither doc defines exactly when a Fixer patch counts as "resolved" —
   AGENT_SPECS.md §3 only says a human/Verifier checks the re-run score,
   and fixer.py's own docstring says that verdict is Core Orchestration's
   job, not Fixer's. The `_verify` closure in `_run_analysis()` below
   applies the exact same yardstick the Bisector used to call the commit
   "regressed" in the first place: back within `regression_threshold_pct`
   of the original baseline, computed in Python rather than via another
   Nano call, since it's a deterministic comparison against numbers this
   process already has.

7. AGENT_SPECS.md §3's input schema types `diagnosis` as "output of
   Diagnoser agent" (a plain dict) — per fixer.py's own calling-convention
   note, this passes `dataclasses.asdict(diagnosis_result)` through
   unchanged rather than re-deriving a different shape.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import statistics
import subprocess
import tempfile
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import structlog
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from dotenv import load_dotenv

# Load backend/.env BEFORE importing modules that read os.environ at import time
# (models.py reads the Nemotron model IDs at import). Real shell env vars still
# win over .env, so a one-off `export` keeps working as an override.
load_dotenv(Path(__file__).with_name(".env"))

from bisector import bisect_repo
from diagnoser import DiagnosisResult, diagnose
from fixer import FixProposal, VerifyOutcome, run_fix_loop_live
from sandbox_client import SandboxError, get_sandbox

log = structlog.get_logger("api")

_DEFAULT_REGRESSION_THRESHOLD_PCT = float(os.environ.get("REGRESSION_THRESHOLD_PCT", "15.0"))
_DEFAULT_N_RUNS = int(os.environ.get("BENCHMARK_N_RUNS", "5"))
_USE_TOKEN_FACTORY = os.environ.get("USE_TOKEN_FACTORY") == "1"

# CORS — CODE_REVIEW_FINDINGS.md #1: the dashboard (frontend/src/lib/api.ts)
# calls this API directly from the browser, from a different origin
# (NEXT_PUBLIC_API_BASE_URL, e.g. localhost:3000 -> localhost:8000). Without
# this, every browser blocks the request outright — invisible in this repo's
# own tests because FastAPI's TestClient doesn't enforce browser CORS at
# all. Env-configurable, not hardcoded, since the real frontend origin
# depends on deployment (a comma-separated list; "*" allowed for local/demo
# use only — credentials are never sent, so a wildcard here doesn't expose
# cookies/auth).
_CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_job_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Schema — matches BUILD_00_OVERVIEW.md's reconciled shape exactly, except
# for the added `error` field (flag #2) and the additive DiagnosisModel
# fields below (flags — CODE_REVIEW_FINDINGS.md #8/#9):
#   - `diff`: not in AGENT_SPECS.md §2 / TRD.md §3's documented output
#     schema at all. Added because BUILD_03_FRONTEND_DASHBOARD.md's
#     drill-down panel needs the guilty commit's diff text to render its
#     "wow moment" (the cited diff hunk) and nothing else provides it —
#     frontend/src/lib/types.ts already models this as optional for
#     exactly this reason. Worth folding into AGENT_SPECS.md §2 properly
#     rather than leaving it an api.py-only addition.
#   - `citation_verification_failed`: present on diagnoser.DiagnosisResult
#     but missing from the documented wire schema — a genuinely useful
#     dashboard/debugging signal (this module forced an "other" downgrade)
#     that had nowhere to go. Worth documenting properly, same as `diff`.
#   - `tavily_refs` is typed as `list[TavilyRefModel]` here, not the
#     `list[dict]` this field used to be typed as before Stack 2 was wired
#     up — diagnoser.DiagnosisResult.tavily_refs is `list[TavilyRef]`, so
#     this now matches field-for-field instead of needing a lossy dict
#     pass-through at the adapter boundary.
# ---------------------------------------------------------------------------

class TimelineEntryModel(BaseModel):
    commit: str
    score: float
    timestamp: str


class TavilyRefModel(BaseModel):
    title: str
    url: str


class DiagnosisModel(BaseModel):
    category: str
    explanation: str
    cited_lines: list[str]
    confidence: Literal["high", "medium", "low"]
    tavily_refs: list[TavilyRefModel] = Field(default_factory=list)
    citation_verification_failed: bool = False
    diff: str | None = None


class FixAttemptModel(BaseModel):
    attempt: int
    patch: str
    rationale: str
    score_after: float
    resolved: bool


class FixModel(BaseModel):
    patch_diff: str
    verified: bool
    before_score: float
    after_score: float


class JobState(BaseModel):
    job_id: str
    status: Literal["queued", "bisecting", "diagnosing", "fixing", "done", "failed"]
    repo_url: str
    benchmark_command: str
    commit_range: list[str] | None = None
    timeline: list[TimelineEntryModel] = Field(default_factory=list)
    regression_commit: str | None = None
    diagnosis: DiagnosisModel | None = None
    fix_attempts: list[FixAttemptModel] = Field(default_factory=list)
    fix: FixModel | None = None
    final_result: Literal["resolved", "unresolved_diagnosis_only"] | None = None
    error: str | None = None  # flagged addition — see module docstring #2
    created_at: str
    updated_at: str


class AnalyzeRequest(BaseModel):
    repo_url: str
    benchmark_command: str
    commit_range: list[str] | None = None  # [start_sha, end_sha]


class AnalyzeResponse(BaseModel):
    job_id: str


def _diagnosis_to_model(d: DiagnosisResult) -> DiagnosisModel:
    """Adapter between diagnoser.DiagnosisResult (the dataclass Stack 2
    actually produces) and DiagnosisModel (the wire schema) — needed
    because the two don't match field-for-field: DiagnosisResult's
    `tavily_refs` is `list[TavilyRef]` (a dataclass), DiagnosisModel's is
    `list[TavilyRefModel]` (a pydantic model); everything else lines up
    1:1 (CODE_REVIEW_FINDINGS.md #9)."""
    return DiagnosisModel(
        category=d.category,
        explanation=d.explanation,
        cited_lines=d.cited_lines,
        confidence=d.confidence,
        tavily_refs=[TavilyRefModel(title=r.title, url=r.url) for r in d.tavily_refs],
        citation_verification_failed=d.citation_verification_failed,
        diff=d.diff or None,
    )


# ---------------------------------------------------------------------------
# JobStore — everything behind one small interface, per BUILD_01 §1, so a
# real datastore later is a one-file change.
# ---------------------------------------------------------------------------

class JobNotFoundError(Exception):
    pass


class JobStore(ABC):
    @abstractmethod
    def create(self, job: JobState) -> None:
        """Raises ValueError if job_id already exists."""

    @abstractmethod
    def get(self, job_id: str) -> JobState:
        """Raises JobNotFoundError if missing."""

    @abstractmethod
    def save(self, job: JobState) -> None:
        """Overwrites the stored state for job.job_id wholesale."""


class InMemoryJobStore(JobStore):
    """The "single in-process store for the hackathon" BUILD_01 explicitly
    says is fine. Thread-safe (a real lock, not just relying on the GIL)
    since FastAPI's BackgroundTasks run sync functions in a thread pool."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()

    def create(self, job: JobState) -> None:
        with self._lock:
            if job.job_id in self._jobs:
                raise ValueError(f"job {job.job_id} already exists")
            self._jobs[job.job_id] = job.model_copy(deep=True)

    def get(self, job_id: str) -> JobState:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            return job.model_copy(deep=True)

    def save(self, job: JobState) -> None:
        with self._lock:
            self._jobs[job.job_id] = job.model_copy(deep=True)


# ---------------------------------------------------------------------------
# Repo resolution — accepts a local path (testing) or a real git remote
# (cloned once per job into a scratch dir). See flag #4 above.
# ---------------------------------------------------------------------------

def _resolve_repo_path(repo_url: str, workdir: Path) -> Path:
    local = Path(repo_url)
    if local.exists() and (local / ".git").exists():
        return local.resolve()
    dest = workdir / "repo"
    try:
        proc = subprocess.run(
            ["git", "clone", "-q", repo_url, str(dest)],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired as e:
        # CODE_REVIEW_FINDINGS.md #7 — the second of two spots that hadn't
        # picked up sandbox_client.py's TimeoutExpired-catching convention;
        # a slow/hanging clone must fail the job cleanly, not escape as a
        # raw subprocess exception.
        raise ValueError(f"git clone of {repo_url} timed out (300s)") from e
    if proc.returncode != 0:
        raise ValueError(f"failed to clone {repo_url}: {proc.stderr}")
    return dest


# ---------------------------------------------------------------------------
# Guilty-commit context — diagnoser.py/fixer.py both accept diff/file
# contents as plain string parameters and stay Nemotron-only (BUILD_02
# §"What you own"); this orchestrator is what actually has to produce them
# from the checked-out repo. See module docstring flag #5.
# ---------------------------------------------------------------------------

def _run_git(args: list[str], *, cwd: Path, timeout: int = 30) -> str:
    """Runs a git command, raising ValueError (caught by _run_analysis's
    generic except-and-fail-the-job handler, same as _resolve_repo_path's
    own ValueErrors) on either a non-zero exit or a timeout — never lets a
    raw subprocess.TimeoutExpired escape uncaught (CODE_REVIEW_FINDINGS.md
    #7's convention, applied to this file's own new git plumbing too)."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise ValueError(f"git {' '.join(args)} timed out ({timeout}s)") from e
    if proc.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


def _commit_diff_and_message(repo_path: Path, commit_sha: str) -> tuple[str, str, list[str]]:
    """Returns (diff, commit_message, changed_file_paths) for commit_sha's
    own change — the diff against its immediate parent. This is the diff
    diagnoser.py's module docstring says it "already receives... as an
    input parameter" (CODE_REVIEW_FINDINGS.md #8) — this is where that
    input actually comes from."""
    parent = f"{commit_sha}^"
    diff = _run_git(["diff", parent, commit_sha], cwd=repo_path)
    message = _run_git(["log", "-1", "--format=%B", commit_sha], cwd=repo_path).strip()
    changed = [
        line for line in _run_git(["diff", "--name-only", parent, commit_sha], cwd=repo_path).splitlines()
        if line.strip()
    ]
    return diff, message, changed


def _concat_file_contents_at_commit(repo_path: Path, commit_sha: str, paths: list[str]) -> str:
    """Per fixer.py's own calling-convention note: "for a multi-file fix,
    the caller is expected to concatenate with clear file-path markers."
    A path git can't show as text at this commit (deleted, binary, or
    otherwise unreadable) is noted rather than failing the whole job over
    one file — partial context is still honest, useful context."""
    parts = []
    for path in paths:
        try:
            contents = _run_git(["show", f"{commit_sha}:{path}"], cwd=repo_path)
        except ValueError:
            parts.append(f"# FILE: {path}\n<unavailable at {commit_sha[:10]} — deleted or not text>")
            continue
        parts.append(f"# FILE: {path}\n{contents}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# The pipeline itself
# ---------------------------------------------------------------------------

def _run_analysis(
    *,
    job_id: str,
    store: JobStore,
    repo_url: str,
    benchmark_command: str,
    start_sha: str,
    end_sha: str,
    regression_threshold_pct: float,
    n_runs: int,
) -> None:
    workdir = Path(tempfile.mkdtemp(prefix=f"prd_job_{job_id}_"))
    try:
        job = store.get(job_id)
        job.status = "bisecting"
        job.updated_at = _now_iso()
        store.save(job)

        repo_path = _resolve_repo_path(repo_url, workdir)
        sandbox = get_sandbox(str(repo_path), use_token_factory=_USE_TOKEN_FACTORY)

        result = bisect_repo(
            job_id=job_id, repo_path=str(repo_path), start_sha=start_sha, end_sha=end_sha,
            benchmark_command=benchmark_command, sandbox=sandbox,
            regression_threshold_pct=regression_threshold_pct, n_runs=n_runs,
        )

        job = store.get(job_id)
        job.timeline = [
            TimelineEntryModel(commit=e.commit, score=e.score, timestamp=e.timestamp)
            for e in result.timeline
        ]
        job.regression_commit = result.regression_commit
        job.updated_at = _now_iso()

        if result.regression_commit is None:
            # Nothing to diagnose — an honest "clean" result (module
            # docstring flag #1), not a fabricated resolution of anything.
            job.status = "done"
            store.save(job)
            log.info("api.job_done", job_id=job_id, regression_commit=None,
                      candidates_evaluated=result.candidates_evaluated)
            return

        job.status = "diagnosing"
        store.save(job)

        diff, commit_message, changed_paths = _commit_diff_and_message(repo_path, result.regression_commit)
        full_file_contents = _concat_file_contents_at_commit(repo_path, result.regression_commit, changed_paths)
        after_score = next(e.score for e in result.timeline if e.commit == result.regression_commit)

        diagnosis_result = diagnose(
            job_id=job_id, guilty_commit_sha=result.regression_commit, diff=diff,
            surrounding_context=full_file_contents, commit_message=commit_message,
            before_score=result.baseline_score, after_score=after_score,
        )

        job = store.get(job_id)
        job.diagnosis = _diagnosis_to_model(diagnosis_result)
        job.updated_at = _now_iso()

        if diagnosis_result.category == "other":
            # An honest "other" diagnosis has no concrete, cited root cause
            # for the Fixer to act on — module docstring flag #1. Skip
            # straight to done; final_result stays None (neither "resolved"
            # nor "unresolved_diagnosis_only" honestly describes "never
            # attempted," and AGENT_SPECS.md doesn't say Fixer should run
            # against category="other" anyway).
            job.status = "done"
            store.save(job)
            log.info("api.job_done", job_id=job_id, regression_commit=result.regression_commit,
                      candidates_evaluated=result.candidates_evaluated, diagnosis_category="other")
            return

        job.status = "fixing"
        store.save(job)

        def _verify(proposal: FixProposal) -> VerifyOutcome:
            try:
                bench_result = sandbox.apply_patch_and_benchmark(
                    result.regression_commit, proposal.patch, benchmark_command, n_runs=n_runs,
                )
            except SandboxError as e:
                if "patch did not apply" not in str(e):
                    raise  # a real sandbox failure still fails the job
                # Model-written diffs are often malformed. That's a failed
                # attempt (the loop retries), not a dead job.
                log.warning("fixer.patch_did_not_apply", job_id=job_id, error=str(e)[-300:])
                regressed_score = next(
                    (t.score for t in result.timeline if t.commit == result.regression_commit),
                    result.baseline_score,
                )
                return VerifyOutcome(score_after=regressed_score, resolved=False)
            score_after = statistics.median(bench_result.raw_scores)
            pct_change = (
                ((score_after - result.baseline_score) / result.baseline_score) * 100
                if result.baseline_score else 0.0
            )
            # Module docstring flag #6: "resolved" means back within the
            # same regression_threshold_pct the Bisector used to call this
            # commit "regressed" in the first place.
            resolved = pct_change <= regression_threshold_pct
            return VerifyOutcome(score_after=score_after, resolved=resolved)

        fix_loop_result = run_fix_loop_live(
            job_id=job_id,
            diagnosis=dataclasses.asdict(diagnosis_result),  # module docstring flag #7
            full_file_contents=full_file_contents,
            verify=_verify,
            before_score=result.baseline_score,
        )

        job = store.get(job_id)
        # FixAttemptModel/FixAttemptRecord already match field-for-field
        # (CODE_REVIEW_FINDINGS.md #9) — a trivial 1:1, no adapter needed.
        job.fix_attempts = [
            FixAttemptModel(
                attempt=a.attempt, patch=a.patch, rationale=a.rationale,
                score_after=a.score_after, resolved=a.resolved,
            )
            for a in fix_loop_result.attempts
        ]
        if fix_loop_result.final_patch is not None:
            job.fix = FixModel(
                patch_diff=fix_loop_result.final_patch,
                verified=fix_loop_result.resolved,
                before_score=fix_loop_result.before_score,
                after_score=(
                    fix_loop_result.after_score
                    if fix_loop_result.after_score is not None
                    else fix_loop_result.before_score
                ),
            )
        job.final_result = "resolved" if fix_loop_result.resolved else "unresolved_diagnosis_only"
        job.status = "done"
        job.updated_at = _now_iso()
        store.save(job)
        log.info("api.job_done", job_id=job_id, regression_commit=result.regression_commit,
                  candidates_evaluated=result.candidates_evaluated, final_result=job.final_result)
    except Exception as e:
        job = store.get(job_id)
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
        job.updated_at = _now_iso()
        store.save(job)
        log.error("api.job_failed", job_id=job_id, error=job.error)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# FastAPI app — factory so tests get an isolated store, not a shared global
# ---------------------------------------------------------------------------

def create_app(store: JobStore | None = None) -> FastAPI:
    store = store or InMemoryJobStore()
    app = FastAPI(title="Performance Regression Detective")

    # CODE_REVIEW_FINDINGS.md #1 — see _CORS_ALLOWED_ORIGINS above for why
    # this exists and how it's configured.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.post("/analyze", response_model=AnalyzeResponse)
    def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks) -> AnalyzeResponse:
        if req.commit_range is None:
            raise HTTPException(
                400,
                "commit_range is required for now. Auto-detecting a range isn't "
                "implemented (see api.py's module docstring, flag #3) — pass "
                "[start_sha, end_sha] explicitly.",
            )
        if len(req.commit_range) != 2:
            raise HTTPException(400, "commit_range must be exactly [start_sha, end_sha]")

        job_id = _new_job_id()
        now = _now_iso()
        job = JobState(
            job_id=job_id, status="queued", repo_url=req.repo_url,
            benchmark_command=req.benchmark_command, commit_range=req.commit_range,
            created_at=now, updated_at=now,
        )
        store.create(job)
        background_tasks.add_task(
            _run_analysis, job_id=job_id, store=store, repo_url=req.repo_url,
            benchmark_command=req.benchmark_command,
            start_sha=req.commit_range[0], end_sha=req.commit_range[1],
            regression_threshold_pct=_DEFAULT_REGRESSION_THRESHOLD_PCT, n_runs=_DEFAULT_N_RUNS,
        )
        return AnalyzeResponse(job_id=job_id)

    @app.get("/jobs/{job_id}", response_model=JobState)
    def get_job(job_id: str) -> JobState:
        try:
            return store.get(job_id)
        except JobNotFoundError:
            raise HTTPException(404, f"job {job_id} not found")

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
