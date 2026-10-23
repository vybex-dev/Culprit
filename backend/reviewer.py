# backend/reviewer.py
"""
reviewer.py — screening a proposed fix before and while it is benchmarked.

"Verified" means a sandbox run came back fast (and green). That is only worth
something if the patch made the CODE faster rather than the MEASUREMENT. Two
layers guard that:

  1. Rules in pure code, no model:
       - HARD: when the job has a test command, a patch may not edit the test
         files that vouch for it. It is rejected as a failed attempt (reason fed
         back to the Fixer) before a sandbox run is spent.
       - FLAG: a patch that edits the benchmark script itself changes what is
         measured. That can be legitimate (the guilty commit changed the
         benchmark, and a toy repo's benchmark may be the code), so it is not
         refused — but it is always flagged "suspicious" with that reason, whether
         or not the model screen is on, so no one reads the speed-up as proof the
         code got faster.

  2. An ADVISORY review by Nemotron 3 Nano — a cheap, high-frequency screen on
     every candidate: does this patch look like it speeds up the real work, or
     does it special-case the benchmark's inputs, hard-code results, skip work
     or validation, or swallow errors? Nano's verdict is shown next to the
     candidate (dashboard, terminal, PR report). It never decides whether a fix
     is verified and never picks a winner — the sandbox does both; a small
     model's opinion must not gate or promote a patch. It is evidence for the
     human who merges.

Split like the other agents: Layer A (pure rules) and Layer B (the Nano call).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

from events import Reporter
from models import call_nemotron

log = structlog.get_logger("reviewer")

REVIEW_TEMPERATURE = 0.1  # a screen, not a brainstorm (lowest of the documented 0.1-0.3 Nano range)
MAX_CONCERNS = 3
_MAX_CONCERN_CHARS = 300

REVIEW_SYSTEM_PROMPT = """You are a careful code reviewer screening a proposed PERFORMANCE FIX before it is
benchmarked. You are given the diagnosis (root cause), the patch (a unified
diff), the benchmark command that will measure it, and whether the project has
a test suite.

Decide whether the patch makes the program genuinely faster at its real work,
or only makes the benchmark look faster. Mark it "suspicious" if it does any of
these: special-cases the benchmark's inputs or the benchmark script; hard-codes
or precomputes results keyed to what the benchmark does; skips, removes or
weakens work, validation, checks or limits the code is supposed to perform;
swallows errors; disables a feature; reduces precision or output; or edits the
benchmark or tests. Reverting the regressing change, batching repeated calls,
caching with correct invalidation, and better algorithms or data structures are
legitimate: mark those "ok".

Judge only what the diff shows; do not speculate about code you cannot see.
For "suspicious", give at most 3 concerns, each one sentence naming the
changed line or construct. For "ok", give an empty list.

Return ONLY JSON matching this schema:
{
  "verdict": "ok" | "suspicious",
  "concerns": ["<one sentence>", ...]
}"""

Verdict = Literal["ok", "suspicious"]


class ReviewError(Exception):
    """The reviewer's response was unusable (schema-invalid)."""


@dataclass
class PatchReview:
    verdict: Verdict
    concerns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "concerns": list(self.concerns)}


# ---------------------------------------------------------------------------
# Layer A — the hard rule (pure)
# ---------------------------------------------------------------------------

def patched_paths(patch: str) -> list[str]:
    """Repo-relative paths a unified diff touches (new, changed or deleted)."""
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            p = line[4:].split("\t")[0].strip()
            if p == "/dev/null":
                continue
            if p[:2] in ("a/", "b/"):
                p = p[2:]
            if p and p not in paths:
                paths.append(p)
    return paths


def _norm(p: str) -> str:
    p = p.strip().strip("'\"")
    while p.startswith("./"):
        p = p[2:]
    return p


def benchmark_targets(benchmark_command: str) -> set[str]:
    """Files the benchmark command runs: `python bench.py …` -> bench.py;
    `python -m pkg.bench` -> pkg/bench.py and pkg/bench/__main__.py."""
    toks = benchmark_command.split()
    out: set[str] = set()
    for i, t in enumerate(toks):
        t = _norm(t)
        if t.endswith(".py") and not t.startswith(("-", "/")) and ".." not in t.split("/"):
            out.add(t)
        if t == "-m" and i + 1 < len(toks):
            mod = toks[i + 1].strip("'\"").replace(".", "/")
            out.update({f"{mod}.py", f"{mod}/__main__.py"})
    return out


_TEST_NAME_RE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|conftest\.py)$")
_TEST_DIR_RE = re.compile(r"(^|/)(tests?|testing)/")


def tests_in_command(test_command: str) -> set[str]:
    """Paths the test command runs: `pytest tests/unit` -> tests/unit;
    `python -m unittest pkg.test_x` -> pkg/test_x.py."""
    toks = [_norm(t) for t in test_command.split()]
    out: set[str] = set()
    for i, t in enumerate(toks):
        if not t or t.startswith("-"):
            continue
        if "/" in t or t.endswith(".py"):
            out.add(t.split("::")[0])
        elif i >= 1 and toks[i - 1] == "unittest" and "." in t:
            out.add(t.replace(".", "/") + ".py")
    return out


def forbidden_edits(patch: str, *, test_command: str | None = None) -> list[tuple[str, str]]:
    """[(path, why)] for every test file in `patch` when the job has a test
    command (the tests are what vouch for the fix, so a fix must not edit them).
    Empty list = the patch may proceed to the sandbox."""
    if not test_command:
        return []
    tests = tests_in_command(test_command)
    bad: list[tuple[str, str]] = []
    for path in patched_paths(patch):
        p = _norm(path)
        if (_TEST_NAME_RE.search(p) or _TEST_DIR_RE.search(p)
                or any(p == t or p.startswith(t.rstrip("/") + "/") for t in tests)):
            bad.append((path, "it is a test file — the tests are what vouch for the fix, so a fix must not edit them"))
    return bad


def benchmark_edits(patch: str, *, benchmark_command: str) -> list[str]:
    """Paths in `patch` that are the benchmark script itself (see module doc: a flag, not a refusal)."""
    bench = benchmark_targets(benchmark_command)
    return [p for p in patched_paths(patch) if _norm(p) in bench]


def benchmark_edit_concern(paths: list[str]) -> str:
    return (f"the patch edits the benchmark script itself ({', '.join(paths)}), which changes what is being measured — "
            "the speed-up may not mean the code got faster")


def rejection_note(bad: list[tuple[str, str]]) -> str:
    return ("the patch was rejected before it was run: "
            + "; ".join(f"it edits {p} ({why})" for p, why in bad)
            + ". Fix the code under test instead, and leave the tests alone.")


# ---------------------------------------------------------------------------
# Layer B — the Nano call (advisory)
# ---------------------------------------------------------------------------

def _validate(parsed: dict) -> PatchReview:
    verdict = parsed.get("verdict")
    if verdict not in ("ok", "suspicious"):
        raise ReviewError(f"reviewer verdict must be 'ok' or 'suspicious', got {verdict!r}")
    raw = parsed.get("concerns", [])
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        raise ReviewError("reviewer 'concerns' must be a list of strings")
    concerns = [c.strip()[:_MAX_CONCERN_CHARS] for c in raw if c.strip()][:MAX_CONCERNS]
    if verdict == "ok":
        concerns = []  # an "ok" with caveats is not a flag; don't show half-flags
    elif not concerns:
        concerns = ["the reviewer flagged this patch but gave no reason"]
    return PatchReview(verdict=verdict, concerns=concerns)


def review_patch(
    *,
    job_id: str,
    diagnosis: dict,
    patch: str,
    benchmark_command: str,
    has_tests: bool,
    attempt: int,
    reporter: Reporter | None = None,
    temperature: float = REVIEW_TEMPERATURE,
) -> PatchReview:
    """One real Nemotron 3 Nano call. Advisory: callers must treat a failure to
    review as "no review", never as approval or rejection."""
    payload = {
        "patch_to_review": patch,
        "diagnosis": {k: diagnosis.get(k) for k in ("category", "explanation", "cited_lines")},
        "benchmark_command": benchmark_command,
        "project_has_tests": has_tests,
    }
    extra = {"reporter": reporter} if reporter is not None else {}
    result = call_nemotron(
        job_id=job_id, step=f"review:attempt={attempt}", model="nano",
        system_prompt=REVIEW_SYSTEM_PROMPT, payload=payload, temperature=temperature, **extra,
    )
    review = _validate(result.parsed)
    log.info("reviewer.result", job_id=job_id, attempt=attempt, verdict=review.verdict)
    return review
