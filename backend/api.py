# backend/api.py
"""
api.py — job endpoints (POST /analyze, GET /jobs/{job_id}), per BUILD_01 §1
and the reconciled schema in BUILD_00_OVERVIEW.md.

Everything Stack 1 owns is wired up for real here: sandbox_client.py's
get_sandbox(), models.py's call_nemotron() (via bisector.py), bisector.py's
bisect_repo(). Diagnoser/Fixer (Stack 2 — diagnoser.py, fixer.py,
tavily_client.py) don't exist in this repo yet, so this build's pipeline
runs exactly as far as BUILD_01's own "Hand back" section describes: "repo
in -> guilty commit + timeline out."

Four judgment calls made here that no doc resolves, flagged rather than
picked silently (AGENTS.md: "don't guess silently — flag it explicitly"):

1. What happens after bisecting completes, given Diagnoser/Fixer aren't
   built yet. The documented schema already makes `diagnosis`, `fix`, and
   `final_result` optional/nullable — so rather than inventing a 7th status
   value, a completed bisection (regression found or not) goes straight to
   status="done" with those three fields left None. That's not a fabricated
   "fully resolved" result — a dashboard reading this state sees exactly
   what happened: a guilty commit and a timeline, nothing claimed beyond
   that. When Stack 2 lands, wiring diagnose_fn/fix_fn into
   `_run_analysis()` below is meant to be a one-file addition, not a
   rewrite — see the TODO there.

2. AGENTS.md requires failing "with a clear error," but the documented
   schema has nowhere to put one. Added one optional field, `error: str |
   None`, used only when status="failed". This is the one place this
   module's job-state shape differs from BUILD_00_OVERVIEW.md's — everything
   else matches exactly.

3. `POST /analyze`'s `commit_range` is documented as optional, but nothing
   says what "auto-detect a range" should mean (last N commits? earliest
   tag to HEAD? something else?) — real design decisions I didn't want to
   guess at. For now, omitting it is a 400, not a silent guess.

4. `repo_url` needs to work for both a real git remote (clone it once, per
   job, into a scratch dir) and a local path (useful for testing without
   network access) — TRD/AGENT_SPECS don't say, so `_resolve_repo_path()`
   below just accepts either.
"""

from __future__ import annotations

import os
import shutil
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
from pydantic import BaseModel, Field

from bisector import bisect_repo
from sandbox_client import SandboxError, get_sandbox

log = structlog.get_logger("api")

_DEFAULT_REGRESSION_THRESHOLD_PCT = float(os.environ.get("REGRESSION_THRESHOLD_PCT", "15.0"))
_DEFAULT_N_RUNS = int(os.environ.get("BENCHMARK_N_RUNS", "5"))
_USE_TOKEN_FACTORY = os.environ.get("USE_TOKEN_FACTORY") == "1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_job_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Schema — matches BUILD_00_OVERVIEW.md's reconciled shape exactly, except
# for the added `error` field (see flag #2 above).
# ---------------------------------------------------------------------------

class TimelineEntryModel(BaseModel):
    commit: str
    score: float
    timestamp: str


class DiagnosisModel(BaseModel):
    """Defined now so Stack 2 can import and populate this directly rather
    than re-deriving the schema — unused until diagnoser.py exists."""
    category: str
    explanation: str
    cited_lines: list[str]
    confidence: Literal["high", "medium", "low"]
    tavily_refs: list[dict] = Field(default_factory=list)


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
    proc = subprocess.run(
        ["git", "clone", "-q", repo_url, str(dest)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise ValueError(f"failed to clone {repo_url}: {proc.stderr}")
    return dest


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
        # TODO(Stack 2): once diagnoser.py/fixer.py exist, this is where
        # diagnosing -> fixing go, only when result.regression_commit is set.
        # diagnosis/fix/final_result stay None until then — see flag #1.
        job.status = "done"
        job.updated_at = _now_iso()
        store.save(job)
        log.info("api.job_done", job_id=job_id, regression_commit=result.regression_commit,
                  candidates_evaluated=result.candidates_evaluated)
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
