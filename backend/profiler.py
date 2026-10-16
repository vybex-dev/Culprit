# FILE: backend/profiler.py — new
"""
profiler.py — measured evidence for the Diagnoser.

The Diagnoser reasons from a diff, which is a *hypothesis*. A profile of the
benchmark on the last good commit and on the guilty one turns it into a
*measurement*: "`DB.query` went from 6 calls to 186" is evidence a human can
check; "this looks like an N+1" is not.

How the profile is taken without touching the user's benchmark command: a tiny
`sitecustomize.py` is put on PYTHONPATH. Python imports it at startup, it starts
`cProfile` and dumps the stats at exit (one file per process). So *any*
`python ...` benchmark command is profiled unchanged — no command rewriting,
no repo changes. A benchmark that isn't Python simply produces no profile, and
the pipeline says so and carries on (profiling is best-effort evidence; it can
never fail a job or be invented).

This module is pure: bytes in, plain dicts out. Sandboxes (sandbox_client.py)
produce the raw `.prof` blobs; everything here runs on the host.
"""

from __future__ import annotations

import os
import pstats
import tempfile
from typing import Any

# Runs inside the sandbox, before the benchmark. Kept dependency-free.
SITECUSTOMIZE = '''\
import atexit, cProfile, os

_out = os.environ.get("CULPRIT_PROFILE_DIR")
if _out:
    _pr = cProfile.Profile()

    def _culprit_dump():
        try:
            _pr.disable()
            _pr.dump_stats(os.path.join(_out, "p%d.prof" % os.getpid()))
        except Exception:
            pass

    atexit.register(_culprit_dump)
    _pr.enable()
'''

MAX_BLOB_BYTES = 4_000_000  # a runaway profile is dropped, not shipped around

_NOISE = ("<frozen", "importlib", "_bootstrap", "<string>", "sitecustomize")


def _func_label(file: str, name: str) -> str:
    """cProfile names builtins like `{built-in method time.sleep}`; keep those
    readable and give Python functions their `file:name`."""
    if name.startswith("{") and name.endswith("}"):
        return name[1:-1]
    return f"{file}:{name}" if file and file != "~" else name


def _relativize(path: str, roots: list[str]) -> tuple[str, bool]:
    """(path relative to the checkout when it lives there, is_project_code)."""
    for root in roots:
        if root and path.startswith(root.rstrip("/") + "/"):
            return path[len(root.rstrip("/")) + 1:], True
    return path, False


def summarize(blobs: list[bytes], *, root: str, top: int = 40) -> dict[str, Any]:
    """Merge the per-process `.prof` blobs into one table of the heaviest
    functions (by cumulative time), with paths relative to the checkout.

    Raises ValueError if there is nothing usable — the caller treats that as
    "no profile", never as an empty-but-valid one.
    """
    blobs = [b for b in blobs if b and len(b) <= MAX_BLOB_BYTES]
    if not blobs:
        raise ValueError("no profile data was produced (is the benchmark a Python program?)")

    paths: list[str] = []
    try:
        for b in blobs:
            fd, p = tempfile.mkstemp(suffix=".prof")
            with os.fdopen(fd, "wb") as f:
                f.write(b)
            paths.append(p)
        try:
            st = pstats.Stats(paths[0])
            for extra in paths[1:]:
                st.add(extra)
        except Exception as e:  # noqa: BLE001 — corrupt/truncated dump
            raise ValueError(f"could not read the profile: {e}") from e
    finally:
        for p in paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    roots = [root]
    try:
        real = os.path.realpath(root)
        if real != root:
            roots.append(real)
    except OSError:
        pass

    rows: list[dict[str, Any]] = []
    total = 0.0
    for (file, line, name), (_cc, nc, tt, ct, _callers) in st.stats.items():  # type: ignore[attr-defined]
        total = max(total, ct)
        if any(n in file or n in name for n in _NOISE):
            continue
        rel, project = _relativize(file, roots)
        rows.append({
            "func": _func_label(rel, name), "file": rel, "line": int(line), "project": project,
            "calls": int(nc), "self_s": round(tt, 6), "cum_s": round(ct, 6),
        })
    if not rows:
        raise ValueError("the profile contained no usable functions")
    rows.sort(key=lambda r: r["cum_s"], reverse=True)
    return {"total_s": round(total, 6), "functions": rows[:top]}


def diff_profiles(before: dict[str, Any], after: dict[str, Any], *, top: int = 8) -> list[dict[str, Any]]:
    """The functions that got the most expensive between the two profiles,
    ranked by added cumulative time. Both runs carry the same profiler
    overhead, so the *comparison* is fair even though absolute times aren't
    the benchmark's real numbers (those come from the unprofiled runs)."""
    b = {(f["func"], f["line"]): f for f in before["functions"]}
    rows: list[dict[str, Any]] = []
    for f in after["functions"]:
        old = b.get((f["func"], f["line"]))
        calls_b = old["calls"] if old else 0
        cum_b = old["cum_s"] if old else 0.0
        self_b = old["self_s"] if old else 0.0
        added = f["cum_s"] - cum_b
        if added <= 0 and f["calls"] <= calls_b:
            continue
        rows.append({
            "func": f["func"], "file": f["file"], "line": f["line"], "project": f["project"],
            "calls_before": calls_b, "calls_after": f["calls"],
            "cum_before_s": round(cum_b, 6), "cum_after_s": round(f["cum_s"], 6),
            "self_before_s": round(self_b, 6), "self_after_s": round(f["self_s"], 6),
            "_added": added,
        })
    rows.sort(key=lambda r: r["_added"], reverse=True)
    for r in rows:
        del r["_added"]
    return rows[:top]


def build_evidence(
    *, before_commit: str, after_commit: str, before: dict[str, Any], after: dict[str, Any],
) -> dict[str, Any]:
    """The one JSON-serializable object that is stored on the job, shown in the
    dashboard/PR report, and handed to the Diagnoser."""
    return {
        "before_commit": before_commit,
        "after_commit": after_commit,
        "before_total_s": before["total_s"],
        "after_total_s": after["total_s"],
        "hotspots": diff_profiles(before, after),
    }
