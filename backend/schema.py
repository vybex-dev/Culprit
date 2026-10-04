# backend/schema.py
"""
schema.py — the job-state wire schema and the storage layer behind it.

Split out of api.py so storage can grow (SQLite persistence, event streams)
without turning the HTTP module into a monolith. api.py re-exports everything
here, so `from api import InMemoryJobStore, JobState, ...` keeps working.

The wire shape is BUILD_00_OVERVIEW.md's reconciled schema, plus *additive*
fields (every one optional/defaulted, so old clients and stored rows stay
valid). Additions, and why:
  error, stage_times, server_time      — see api.py history (failure + live clocks)
  mode                                 — "live" | "offline": whether model roles
                                         were real Nemotron calls or the labelled
                                         offline stand-in (offline.py)
  commits                              — metadata for every commit in the range,
                                         so the UI can draw the whole history
  probes                               — each benchmarked commit's raw runs, verdict
                                         and Nano calls (the evidence behind the chart)
  baseline_score/threshold_pct/n_runs  — the yardstick, so the UI can draw it
  window                               — the bisect search window still under suspicion
  cancel_requested                     — cooperative cancel flag
  metrics                              — usage summary derived from the event stream
  status "cancelled"                   — a user-stopped job is neither done nor failed
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

from events import Event

JobStatusLiteral = Literal["queued", "bisecting", "diagnosing", "fixing", "done", "failed", "cancelled"]
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})


class TimelineEntryModel(BaseModel):
    commit: str
    score: float
    timestamp: str


class TavilyRefModel(BaseModel):
    title: str
    url: str
    snippet: str = ""
    score: float | None = None


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
    # Non-empty ⇒ the attempt never produced a measurement (patch didn't apply,
    # or the patched code crashed) and `score_after` must not be read as one.
    error: str | None = None


class FixModel(BaseModel):
    patch_diff: str
    verified: bool
    before_score: float
    after_score: float


class CommitModel(BaseModel):
    index: int
    sha: str
    subject: str = ""
    author: str = ""
    date: str = ""


class ProbeModel(BaseModel):
    step: int
    commit: str
    index: int
    role: Literal["baseline", "endpoint", "bisect"]
    raw_scores: list[float]
    median_score: float
    pct_change: float
    verdict: str
    rounds: int = 1
    nano: list[dict[str, Any]] = Field(default_factory=list)
    window: list[int] | None = None
    timestamp: str = ""
    wall_s: float = 0.0


class JobState(BaseModel):
    job_id: str
    status: JobStatusLiteral
    repo_url: str
    benchmark_command: str
    commit_range: list[str] | None = None
    timeline: list[TimelineEntryModel] = Field(default_factory=list)
    regression_commit: str | None = None
    diagnosis: DiagnosisModel | None = None
    fix_attempts: list[FixAttemptModel] = Field(default_factory=list)
    fix: FixModel | None = None
    final_result: Literal["resolved", "unresolved_diagnosis_only"] | None = None
    error: str | None = None
    created_at: str
    updated_at: str
    stage_times: dict[str, str] = Field(default_factory=dict)
    server_time: str | None = None
    # --- additive (see module docstring) ---
    mode: Literal["live", "offline"] = "live"
    commits: list[CommitModel] = Field(default_factory=list)
    probes: list[ProbeModel] = Field(default_factory=list)
    baseline_score: float | None = None
    threshold_pct: float | None = None
    n_runs: int | None = None
    window: list[int] | None = None
    cancel_requested: bool = False
    metrics: dict[str, Any] | None = None
    label: str | None = None


class JobSummary(BaseModel):
    """Compact row for the history list."""
    job_id: str
    status: JobStatusLiteral
    repo_url: str
    created_at: str
    updated_at: str
    mode: Literal["live", "offline"] = "live"
    regression_commit: str | None = None
    final_result: Literal["resolved", "unresolved_diagnosis_only"] | None = None
    category: str | None = None
    before_score: float | None = None
    regressed_score: float | None = None
    after_score: float | None = None
    n_commits: int = 0
    n_probes: int = 0
    subject: str | None = None
    label: str | None = None


def summarize_job(job: JobState) -> JobSummary:
    regressed = next((t.score for t in job.timeline if t.commit == job.regression_commit), None)
    subject = next((c.subject for c in job.commits if c.sha == job.regression_commit), None)
    return JobSummary(
        job_id=job.job_id, status=job.status, repo_url=job.repo_url, created_at=job.created_at,
        updated_at=job.updated_at, mode=job.mode, regression_commit=job.regression_commit,
        final_result=job.final_result, category=job.diagnosis.category if job.diagnosis else None,
        before_score=job.baseline_score, regressed_score=regressed,
        after_score=job.fix.after_score if job.fix else None,
        n_commits=len(job.commits), n_probes=max(0, len(job.probes) - 1), subject=subject, label=job.label,
    )


# ---------------------------------------------------------------------------
# Job stores
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

    def list(self, limit: int = 50) -> list[JobState]:  # noqa: A003 — mirrors dict-like API
        """Newest first. Optional for minimal stores."""
        return []

    def mark_orphans_failed(self, reason: str) -> int:
        """Jobs left mid-run by a dead process can never finish; mark them failed
        (honestly, with a reason) instead of showing a spinner forever."""
        return 0


class InMemoryJobStore(JobStore):
    """The "single in-process store for the hackathon" BUILD_01 says is fine.
    Thread-safe (a real lock, not just the GIL) since BackgroundTasks run sync
    functions in a thread pool."""

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

    def list(self, limit: int = 50) -> list[JobState]:  # noqa: A003
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [j.model_copy(deep=True) for j in jobs[:limit]]

    def mark_orphans_failed(self, reason: str) -> int:
        return 0  # in-memory jobs die with the process — nothing to recover


class SqliteJobStore(JobStore):
    """Durable store: jobs survive restarts, and so does the history page.
    One JSON document per job (the wire schema is the source of truth), plus a
    few indexed columns for listing. Safe for the threaded server: a single
    connection guarded by a lock, WAL mode for readers-during-writes."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                " job_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
                " status TEXT NOT NULL, body TEXT NOT NULL)"
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at DESC)")
            self._db.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._db

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    def create(self, job: JobState) -> None:
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO jobs(job_id, created_at, updated_at, status, body) VALUES (?,?,?,?,?)",
                    (job.job_id, job.created_at, job.updated_at, job.status, job.model_dump_json()),
                )
                self._db.commit()
            except sqlite3.IntegrityError as e:
                raise ValueError(f"job {job.job_id} already exists") from e

    def get(self, job_id: str) -> JobState:
        with self._lock:
            row = self._db.execute("SELECT body FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return JobState.model_validate_json(row[0])

    def save(self, job: JobState) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO jobs(job_id, created_at, updated_at, status, body) VALUES (?,?,?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET updated_at=excluded.updated_at, status=excluded.status, body=excluded.body",
                (job.job_id, job.created_at, job.updated_at, job.status, job.model_dump_json()),
            )
            self._db.commit()

    def list(self, limit: int = 50) -> list[JobState]:  # noqa: A003
        with self._lock:
            rows = self._db.execute("SELECT body FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [JobState.model_validate_json(r[0]) for r in rows]

    def mark_orphans_failed(self, reason: str) -> int:
        from events import now_iso

        with self._lock:
            rows = self._db.execute(
                "SELECT body FROM jobs WHERE status NOT IN ('done','failed','cancelled')"
            ).fetchall()
        n = 0
        for (body,) in rows:
            job = JobState.model_validate_json(body)
            job.status = "failed"
            job.error = reason
            job.updated_at = now_iso()
            job.stage_times.setdefault("failed", job.updated_at)
            self.save(job)
            n += 1
        return n


# ---------------------------------------------------------------------------
# Event stores — the append-only per-job activity log (events.py)
# ---------------------------------------------------------------------------

class EventStore(ABC):
    @abstractmethod
    def append(self, job_id: str, event: Event) -> None: ...

    @abstractmethod
    def since(self, job_id: str, after: int = 0, limit: int = 1000) -> list[Event]:
        """Events with seq > after, oldest first."""

    def all(self, job_id: str) -> list[Event]:
        return self.since(job_id, 0, 1_000_000)

    def of_kinds(self, job_id: str, kinds: Iterable[str]) -> list[Event]:
        """Only events whose kind is in `kinds` — lets metrics be computed on
        every poll without re-reading the whole (large) stream."""
        wanted = set(kinds)
        return [e for e in self.all(job_id) if e.kind in wanted]

    def last_seq(self, job_id: str) -> int:
        evs = self.since(job_id, 0, 1_000_000)
        return evs[-1].seq if evs else 0


class InMemoryEventStore(EventStore):
    def __init__(self) -> None:
        self._events: dict[str, list[Event]] = {}
        self._lock = threading.Lock()

    def append(self, job_id: str, event: Event) -> None:
        with self._lock:
            self._events.setdefault(job_id, []).append(event)

    def since(self, job_id: str, after: int = 0, limit: int = 1000) -> list[Event]:
        with self._lock:
            return [e for e in self._events.get(job_id, []) if e.seq > after][:limit]


class SqliteEventStore(EventStore):
    """Shares the job store's connection (and lock), so a job and its events
    live in one database file."""

    def __init__(self, job_store: SqliteJobStore) -> None:
        self._db = job_store.connection
        self._lock = job_store.lock
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                " job_id TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL,"
                " source TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL,"
                " PRIMARY KEY (job_id, seq))"
            )
            self._db.commit()

    def append(self, job_id: str, event: Event) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO events(job_id, seq, ts, kind, source, message, data) VALUES (?,?,?,?,?,?,?)",
                (job_id, event.seq, event.ts, event.kind, event.source, event.message, json.dumps(event.data, default=str)),
            )
            self._db.commit()

    def since(self, job_id: str, after: int = 0, limit: int = 1000) -> list[Event]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, ts, kind, source, message, data FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?",
                (job_id, after, limit),
            ).fetchall()
        return [Event(seq=r[0], ts=r[1], kind=r[2], source=r[3], message=r[4], data=json.loads(r[5])) for r in rows]

    def of_kinds(self, job_id: str, kinds: Iterable[str]) -> list[Event]:
        kinds = list(kinds)
        marks = ",".join("?" for _ in kinds)
        with self._lock:
            rows = self._db.execute(
                f"SELECT seq, ts, kind, source, message, data FROM events WHERE job_id=? AND kind IN ({marks}) ORDER BY seq",
                (job_id, *kinds),
            ).fetchall()
        return [Event(seq=r[0], ts=r[1], kind=r[2], source=r[3], message=r[4], data=json.loads(r[5])) for r in rows]

    def last_seq(self, job_id: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT MAX(seq) FROM events WHERE job_id=?", (job_id,)).fetchone()
        return int(row[0] or 0)
