# backend/events.py
"""
events.py — the live activity stream.

Why this exists: the dashboard's terminal used to be *derived* from the final
job JSON, so it could only ever replay a handful of coarse facts. A real
terminal needs a real, append-only log of what the pipeline is doing right
now: every git call, every sandbox run as it lands, every Nemotron request
(with the exact payload and raw response), every citation check, every
Tavily query. That is what this module carries.

Design rules (they mirror AGENTS.md rule 1 — never fabricate):
  * An event is emitted by the code that actually did the thing, at the
    moment it did it. Nothing is scripted or replayed.
  * Events are append-only and numbered per job (`seq`), so a client can poll
    `GET /jobs/{id}/events?after=<seq>` and never miss or double-render one.
  * Long-running operations are *spans*: a `.start` event and a matching
    `.end` event sharing a span id. The UI shows an in-flight spinner between
    the two — that is a real "this is running right now", not decoration.
  * Reporters are optional everywhere. Passing none (as every pre-existing
    test does) is a no-op, so instrumentation never changes behavior.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

# Prompts/responses can be large (Ultra sees whole files). The stream keeps
# enough to be genuinely inspectable without letting one event balloon the
# job database or the polling payload.
MAX_STR_CHARS = 12_000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class JobCancelled(Exception):
    """Raised at a cooperative checkpoint after the user cancelled the job."""


@dataclass
class Event:
    seq: int
    ts: str
    kind: str
    source: str  # "system" | "git" | "bisect" | "sandbox" | "nano" | "ultra" | "tavily" | "fix"
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clip(value: Any, limit: int = MAX_STR_CHARS) -> Any:
    """Recursively truncate oversized strings so events stay small."""
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + f"… [+{len(value) - limit} chars truncated]"
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip(v, limit) for v in value]
    return value


Sink = Callable[[Event], None]


class Reporter:
    """Per-job emitter. Thread-safe. `sink` persists/broadcasts each event."""

    def __init__(
        self,
        job_id: str,
        sink: Sink | None = None,
        cancel: threading.Event | None = None,
        start_seq: int = 0,
    ) -> None:
        self.job_id = job_id
        self._sink = sink
        self._cancel = cancel
        self._seq = start_seq
        self._lock = threading.Lock()
        self._spans: dict[str, tuple[float, dict[str, Any]]] = {}

    # -- plain events -------------------------------------------------------
    def emit(self, kind: str, source: str, message: str, **data: Any) -> Event:
        with self._lock:
            self._seq += 1
            ev = Event(seq=self._seq, ts=now_iso(), kind=kind, source=source, message=message, data=_clip(data))
        if self._sink is not None:
            # A broken sink must never take the pipeline down with it.
            try:
                self._sink(ev)
            except Exception:  # noqa: BLE001
                pass
        return ev

    # -- spans --------------------------------------------------------------
    def start(self, kind: str, source: str, message: str, **data: Any) -> str:
        """Open a span. Emits `<kind>.start`; returns the span id."""
        span = uuid.uuid4().hex[:8]
        with self._lock:
            self._spans[span] = (time.perf_counter(), {"kind": kind, "source": source})
        self.emit(f"{kind}.start", source, message, span=span, **data)
        return span

    def end(self, span: str, message: str, **data: Any) -> Event:
        """Close a span opened by start(). Emits `<kind>.end` with dur_s."""
        with self._lock:
            started, meta = self._spans.pop(span, (time.perf_counter(), {"kind": "span", "source": "system"}))
        dur = round(time.perf_counter() - started, 3)
        return self.emit(f"{meta['kind']}.end", meta["source"], message, span=span, dur_s=dur, **data)

    # -- cancellation -------------------------------------------------------
    def check_cancelled(self) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise JobCancelled("cancelled by user")


class _NullReporter(Reporter):
    """Drop-in that records nothing. Used when a caller passes no reporter."""

    def __init__(self) -> None:
        super().__init__("-", None, None)

    def emit(self, kind: str, source: str, message: str, **data: Any) -> Event:  # type: ignore[override]
        return Event(seq=0, ts="", kind=kind, source=source, message=message)

    def start(self, kind: str, source: str, message: str, **data: Any) -> str:  # type: ignore[override]
        return ""

    def end(self, span: str, message: str, **data: Any) -> Event:  # type: ignore[override]
        return Event(seq=0, ts="", kind="", source="", message=message)


NULL_REPORTER: Reporter = _NullReporter()


def or_null(reporter: Reporter | None) -> Reporter:
    return reporter if reporter is not None else NULL_REPORTER


# ---------------------------------------------------------------------------
# Metrics — derived from the event stream, never stored separately, so the
# numbers on the dashboard can only ever be a sum of things that happened.
# ---------------------------------------------------------------------------

def summarize(events: list[Event] | list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate usage for the 'under the hood' panel from real events."""
    models: dict[str, dict[str, Any]] = {}
    sandbox = {"probes": 0, "runs": 0, "patched_runs": 0, "instances": 0, "run_wall_s": 0.0}
    tavily = {"searches": 0, "sources": 0}
    git_calls = 0

    for raw in events:
        e = raw if isinstance(raw, dict) else raw.to_dict()
        kind, data = e["kind"], e.get("data") or {}
        if kind == "model.end":
            role = data.get("role", e["source"])
            m = models.setdefault(
                role,
                {"role": role, "model_id": data.get("model_id", ""), "calls": 0, "latency_s": 0.0,
                 "prompt_tokens": 0, "completion_tokens": 0, "retries": 0, "offline": False},
            )
            m["calls"] += 1
            m["latency_s"] = round(m["latency_s"] + float(data.get("latency_s") or 0.0), 3)
            usage = data.get("usage") or {}
            m["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            m["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            m["retries"] += max(0, int(data.get("attempts") or 1) - 1)
            m["model_id"] = data.get("model_id", m["model_id"])
            m["offline"] = m["offline"] or bool(data.get("offline"))
        elif kind == "sandbox.run":
            sandbox["runs"] += 1
            sandbox["run_wall_s"] = round(sandbox["run_wall_s"] + float(data.get("wall_s") or 0.0), 3)
            if data.get("patched"):
                sandbox["patched_runs"] += 1
        elif kind == "probe.end":
            sandbox["probes"] += 1
        elif kind == "sandbox.instance.end":
            sandbox["instances"] += 1
        elif kind == "tavily.end":
            tavily["searches"] += 1
            tavily["sources"] += int(data.get("n_refs") or 0)
        elif kind == "git.call":
            git_calls += 1

    for m in models.values():
        m["total_tokens"] = m["prompt_tokens"] + m["completion_tokens"]
        m["avg_latency_s"] = round(m["latency_s"] / m["calls"], 3) if m["calls"] else 0.0
    return {"models": models, "sandbox": sandbox, "tavily": tavily, "git_calls": git_calls, "events": len(events)}


# ---------------------------------------------------------------------------
# Sandbox → event translation. Lives here so the Bisector (probing commits) and
# the Fixer's verify step (benchmarking patched commits) share one vocabulary.
# ---------------------------------------------------------------------------

def sandbox_listener(reporter: Reporter, get_context: Callable[[], dict[str, Any]] | None = None):
    """Build a Sandbox.listener that turns backend callbacks into events.

    `get_context` supplies per-call context (e.g. the current probe step) so
    the terminal can group runs under the probe that launched them.
    """

    def _listener(kind: str, data: dict[str, Any]) -> None:
        ctx = get_context() if get_context else {}
        payload = {**ctx, **data}
        if kind == "run":
            tag = " (patched)" if data.get("patched") else ""
            reporter.emit("sandbox.run", "sandbox", f"run {data['run']}/{data['n_runs']} → {data['score']:.3f} ms{tag}", **payload)
        elif kind == "checkout":
            reporter.emit("sandbox.checkout", "sandbox", f"checked out {str(data.get('commit', ''))[:8]}", **payload)
        elif kind == "patch":
            reporter.emit("sandbox.patch", "sandbox", "patch applied cleanly", **payload)
        elif kind == "deps":
            hit = data.get("cache_hit")
            reporter.emit(
                "sandbox.deps", "sandbox",
                "dependencies: cache hit" if hit else "dependencies: installed (cache miss)", **payload,
            )
        elif kind == "instance":
            if data.get("phase") == "start":
                reporter.emit(
                    "sandbox.instance.start", "sandbox",
                    f"Token Factory sandbox {str(data.get('operation', ''))[:8]} started (VM-isolated, disposable)",
                    span=data.get("operation"), **payload,
                )
            else:
                reporter.emit(
                    "sandbox.instance.end", "sandbox",
                    f"sandbox {str(data.get('operation', ''))[:8]} → {data.get('status')}",
                    span=data.get("operation"), **payload,
                )

    return _listener
