# backend/offline.py
"""
offline.py — a deliberately small, clearly-labelled stand-in for the three
Nemotron roles, used ONLY when CULPRIT_OFFLINE=1.

Why it exists
  * Anyone can clone the repo and watch the *entire* pipeline run — real git,
    real sandbox subprocesses, real benchmark timings, the live terminal, the
    scanner, the fix verification — with zero API keys. (Hackathon rule:
    "a judge can reproduce at least one demo case end-to-end".)
  * CI and local development don't burn Token Factory credits.

What it is NOT
  * It is not Nemotron and never pretends to be. Every event carries
    `offline: true` and model_id `offline-stub/<role>`; the job state has
    `mode: "offline"`; the dashboard shows a persistent banner.
  * It never touches benchmark numbers. Those always come from real runs
    (AGENTS.md rule 1). Only the *reasoning* is scripted.
  * It is never selected implicitly. A missing NEBIUS_API_KEY in normal mode is
    still a hard error, not a silent fallback to this module.

The stand-ins follow the exact same JSON contracts as AGENT_SPECS.md, so the
production validation paths (schema checks, citation verification, patch
building, sandbox verification) run for real against their output.
"""

from __future__ import annotations

import math
import re
import statistics
from typing import Any

_LOOP_RE = re.compile(r"^\s*for\s+.+\s+in\s+.+:\s*$")
_CALL_HINTS = (".query(", ".execute(", ".filter(", ".fetch", ".get(", "requests.", "http", ".find(")
_CACHE_HINTS = ("cache", "memo", "lru_cache", "@cached")


def offline_call(model: str, payload: dict[str, Any]) -> dict[str, Any]:
    if model == "nano":
        return _nano_verdict(payload)
    if "full_file_contents" in payload:
        return _fixer(payload)
    if "diff" in payload:
        return _diagnoser(payload)
    raise ValueError("offline stand-in: unrecognised payload shape")


# ---------------------------------------------------------------------------
# Nano — the documented Bisector policy (AGENT_SPECS.md §1), as arithmetic.
# ---------------------------------------------------------------------------

def _nano_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    scores: list[float] = [float(x) for x in payload["candidate_scores"]]
    baseline = float(payload["baseline_score"])
    threshold = float(payload["regression_threshold_pct"])
    median = statistics.median(scores)
    pct = (median - baseline) / baseline * 100 if baseline else 0.0
    if len(scores) < 2:
        return {"verdict": "inconclusive", "median_score": median,
                "pct_change_from_baseline": pct, "additional_runs_needed": 5}
    se_pct = (statistics.stdev(scores) / baseline * 100) / math.sqrt(len(scores)) if baseline else 0.0
    if pct - 2 * se_pct < threshold < pct + 2 * se_pct:
        return {"verdict": "inconclusive", "median_score": median,
                "pct_change_from_baseline": pct, "additional_runs_needed": 5}
    return {"verdict": "regressed" if pct > threshold else "clean", "median_score": median,
            "pct_change_from_baseline": pct, "additional_runs_needed": 0}


# ---------------------------------------------------------------------------
# Ultra / Diagnoser — pattern rules over the *actual* diff. Citations are
# copied verbatim from real changed lines, so the production citation check
# genuinely verifies them.
# ---------------------------------------------------------------------------

def _changed(diff: str) -> tuple[list[str], list[str]]:
    added, removed = [], []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
    return added, removed


def _diagnoser(payload: dict[str, Any]) -> dict[str, Any]:
    added, removed = _changed(payload["diff"])

    # n_plus_one: a query/call was ADDED inside a loop. The loop header itself
    # is often unchanged context in the diff (only the body changed), so walk
    # the whole diff — context included — but cite only lines that actually
    # changed (the production citation check would reject anything else).
    loop_header: tuple[str, int, bool] | None = None  # (text, indent, was_added)
    for raw in payload["diff"].splitlines():
        if raw.startswith(("+++", "---", "@@", "diff ", "index ")):
            loop_header = None
            continue
        marker, text = (raw[:1], raw[1:]) if raw[:1] in ("+", "-", " ") else (" ", raw)
        if marker == "-":
            continue
        indent = len(text) - len(text.lstrip())
        if _LOOP_RE.match(text):
            loop_header = (text, indent, marker == "+")
            continue
        if (
            marker == "+" and loop_header is not None and indent > loop_header[1]
            and any(h in text for h in _CALL_HINTS)
        ):
            cited = [text] if not loop_header[2] else [loop_header[0], text]
            return {
                "category": "n_plus_one",
                "explanation": (
                    f"Inside the loop `{loop_header[0].strip()}`, the change now runs `{text.strip()}` once per "
                    "iteration, replacing the earlier batched lookup with one call per item. With N items this makes "
                    "N+1 round-trips, which is what slowed the benchmark. "
                    "[offline stand-in classifier — not Nemotron]"
                ),
                "cited_lines": cited,
                "confidence": "medium",
            }

    # lost_cache: a caching construct was removed and nothing replaced it.
    for line in removed:
        if any(h in line.lower() for h in _CACHE_HINTS) and not any(
            h in a.lower() for a in added for h in _CACHE_HINTS
        ):
            return {
                "category": "lost_cache",
                "explanation": (
                    f"The change removes `{line.strip()}` and adds no replacement, so results that used to be "
                    "reused are now recomputed on every call. [offline stand-in classifier — not Nemotron]"
                ),
                "cited_lines": [line],
                "confidence": "medium",
            }

    return {
        "category": "other",
        "explanation": (
            "The offline stand-in classifier found no known pattern (N+1, lost cache) in this diff, so it "
            "honestly declines to name a specific root cause. [offline stand-in classifier — not Nemotron]"
        ),
        "cited_lines": [],
        "confidence": "low",
    }


# ---------------------------------------------------------------------------
# Ultra / Fixer — reverts the guilty hunks. Deterministic, and the result is
# still verified by a *real* sandbox re-run: if reverting doesn't recover the
# benchmark, the job honestly reports "unresolved".
# ---------------------------------------------------------------------------

def _revert_hunks(diff: str) -> list[tuple[str, str, str]]:
    """[(file, new_side_text, old_side_text)] — one per hunk.

    A hunk's "new side" is its context + added lines; its "old side" is its
    context + removed lines. Replacing the first with the second reverts the
    hunk *exactly*, even when the change is a deletion in one place and an
    insertion in another (which a per-line revert can't do)."""
    hunks: list[tuple[str, str, str]] = []
    path = ""
    new_side: list[str] | None = None
    old_side: list[str] = []

    def flush() -> None:
        nonlocal new_side, old_side
        if new_side is not None and (new_side != old_side):
            hunks.append((path, "\n".join(new_side), "\n".join(old_side)))
        new_side, old_side = None, []

    for line in diff.splitlines():
        if line.startswith("+++ "):
            flush()
            p = line[4:].strip()
            path = p[2:] if p.startswith("b/") else p
        elif line.startswith("--- ") or line.startswith("diff ") or line.startswith("index "):
            flush()
        elif line.startswith("@@"):
            flush()
            new_side, old_side = [], []
        elif new_side is not None and line[:1] in ("+", "-", " "):
            body = line[1:]
            if line[0] in ("+", " "):
                new_side.append(body)
            if line[0] in ("-", " "):
                old_side.append(body)
        elif new_side is not None:
            flush()  # e.g. "\ No newline…" or a truncation marker ends the hunk
    flush()
    return hunks


def _fixer(payload: dict[str, Any]) -> dict[str, Any]:
    diagnosis = payload.get("diagnosis") or {}
    diff = diagnosis.get("diff") or ""
    edits = [
        {"file": path, "old": new_side, "new": old_side}
        for path, new_side, old_side in _revert_hunks(diff)
    ]
    if not edits:
        edits = [{"file": "", "old": "", "new": ""}]  # → PatchBuildError → an honest failed attempt
    return {
        "edits": edits,
        "rationale": (
            "Reverts the guilty commit's hunk so the code goes back to its previous, faster form. "
            "[offline stand-in fixer — not Nemotron; the result is still verified by a real sandbox re-run]"
        ),
        "confidence_will_resolve": "medium",
    }
