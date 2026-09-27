# FILE: backend/fixer.py — place at this path in the Culprit repo
# (replaces the Stack-2 stub of the same name).
"""
fixer.py — patch generation + retry loop (max 3 attempts). Calls Nemotron 3
Ultra.

Spec: docs/build-prompts/BUILD_02_BACKEND_AGENTS.md §2
Schema: docs/AGENT_SPECS.md §3

This file proposes patches AND drives the retry loop's bookkeeping — but it
never decides whether a patch worked. That verdict is always the caller's
`verify` callable, never a value this module computes:
"Verification (applying the patch in a sandbox and re-running the
benchmark) is the Core Orchestration stack's job via sandbox_client.py, not
this file's — fixer.py proposes, it never marks itself as verified."

Split into two layers, deliberately mirroring bisector.py's Layer A/B split
(same reasoning: a pure, fully-injectable core that's unit-testable without
a live API key or a real sandbox, plus a thin "real" layer that wires it to
Nemotron):

  Layer A (pure loop): `run_fix_loop()`. Takes `propose` and `verify` as
  plain injected callables — no reference to Nemotron, sandboxes, job_id,
  or diagnosis/file contents at all. This is what's actually testable
  without any live dependency.

  Layer B (real Nemotron wiring): `propose_patch()` (one real call) and
  `run_fix_loop_live()` (wires a real `propose` closure around
  propose_patch(), but STILL takes `verify` as an injected parameter — see
  the flagged gap below).

sandbox_client.py's Sandbox ABC now exposes
`apply_patch_and_benchmark(commit_sha, patch, benchmark_command, n_runs)` —
the "apply this arbitrary patch to a worktree, then benchmark it" method
this docstring used to flag as missing (CODE_REVIEW_FINDINGS.md #10: that
flag had gone stale, since the method existed and was fully tested but
nothing said so here). `verify` is still an injected parameter on
`run_fix_loop_live()` below, by design, not because the real
implementation is missing: the live `verify` closure built on
`apply_patch_and_benchmark()` lives in api.py's `_run_analysis()`, since
building it needs the job's sandbox handle, benchmark command, and
regression threshold — none of which this module owns or imports
(BUILD_02 §"What you own" lists only diagnoser.py/fixer.py/
tavily_client.py). Keeping `verify` as a plain injected callable here is
also what makes `run_fix_loop`/`run_fix_loop_live` testable without a real
sandbox at all.

Calling convention for `diagnosis` (flagged, not pinned by either doc):
AGENT_SPECS.md §3's input schema types `diagnosis` as literally "output of
Diagnoser agent" — a plain dict, not diagnoser.DiagnosisResult. To keep
fixer.py independent of diagnoser.py (no cross-import between the two
Stack-2 agent files), callers should pass
`dataclasses.asdict(diagnosis_result)` here. `full_file_contents` is a
single string per the documented schema; for a multi-file fix, the caller
is expected to concatenate with clear file-path markers (e.g.
"# FILE: path/a.py\\n<contents>\\n\\n# FILE: path/b.py\\n<contents>") since
neither doc defines a multi-file representation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

import structlog

from models import call_nemotron

log = structlog.get_logger("fixer")

Confidence = Literal["high", "medium", "low"]
_CONFIDENCE_LEVELS: frozenset[str] = frozenset({"high", "medium", "low"})

FIXER_TEMPERATURE = 0.2  # within AGENTS.md #4's documented 0.1-0.3 for the Fixer

# Verbatim from AGENT_SPECS.md §3 / BUILD_02 §2 — don't edit here without
# updating that doc first (AGENTS.md #3).
FIXER_SYSTEM_PROMPT = """You are a senior engineer fixing a diagnosed performance regression.
You will be given the diagnosis (root cause + cited lines), the full
contents of the affected file(s), and — on retries — the result of your
previous attempt.

Propose a minimal patch that addresses the specific root cause. Do not
make unrelated changes. If this is a retry and your previous attempt did
not resolve the regression, explain what you're changing about your
approach before proposing the new patch.

Return ONLY JSON matching this schema:
{
  "patch": "<unified diff format>",
  "rationale": "<1-3 sentences on why this fixes the root cause>",
  "confidence_will_resolve": "high" | "medium" | "low"
}"""


class FixerError(Exception):
    """Raised only when Nemotron's response is schema-invalid (missing
    field, empty patch/rationale, or an unknown confidence value) — never
    for "the patch didn't work," which is a normal, expected loop outcome
    (see FixLoopResult.resolved), not an error."""


@dataclass
class FixProposal:
    patch: str
    rationale: str
    confidence_will_resolve: Confidence


@dataclass
class PreviousAttemptResult:
    """Mirrors AGENT_SPECS.md §3's previous_attempt_result input shape
    exactly, so it serializes straight into the next call's payload."""
    patch_applied: str
    score_after_fix: float
    still_regressed: bool


@dataclass
class VerifyOutcome:
    """What an injected `verify` callable must return. `resolved` is the
    ONLY source of truth for whether a fix worked — see module docstring's
    "never marks itself as verified.\""""
    score_after: float
    resolved: bool


@dataclass
class FixAttemptRecord:
    """Field names/order deliberately match api.py's FixAttemptModel
    exactly (attempt, patch, rationale, score_after, resolved) so
    converting one attempt into the job-state's fix_attempts entry is a
    trivial 1:1 mapping, no renaming, at whatever integration point wires
    this into api.py."""
    attempt: int
    patch: str
    rationale: str
    score_after: float
    resolved: bool


@dataclass
class FixLoopResult:
    attempts: list[FixAttemptRecord]
    resolved: bool
    final_patch: str | None  # last attempted patch — None only if attempts is empty
    before_score: float
    after_score: float | None  # last attempt's score_after — None only if attempts is empty


def _validate_schema(parsed: dict, *, attempt_number: int) -> FixProposal:
    """Same "models.py only guarantees valid JSON, not this schema" check
    as bisector.py's _validate_nano_verdict / diagnoser.py's
    _validate_schema."""
    required = {"patch", "rationale", "confidence_will_resolve"}
    missing = required - parsed.keys()
    if missing:
        raise FixerError(f"Fixer response for attempt {attempt_number} missing fields {sorted(missing)}: {parsed!r}")

    patch = parsed["patch"]
    if not isinstance(patch, str) or not patch.strip():
        raise FixerError(f"Fixer patch must be a non-empty string for attempt {attempt_number}: {parsed!r}")

    rationale = parsed["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise FixerError(f"Fixer rationale must be a non-empty string for attempt {attempt_number}: {parsed!r}")

    confidence = parsed["confidence_will_resolve"]
    if confidence not in _CONFIDENCE_LEVELS:
        raise FixerError(
            f"Fixer returned unknown confidence_will_resolve {confidence!r} for attempt {attempt_number}"
        )

    return FixProposal(patch=patch, rationale=rationale, confidence_will_resolve=confidence)


# ---------------------------------------------------------------------------
# Layer A — pure retry loop. No Nemotron, no sandbox — fully injectable.
# ---------------------------------------------------------------------------

def run_fix_loop(
    *,
    propose: Callable[[int, PreviousAttemptResult | None], FixProposal],
    verify: Callable[[FixProposal], VerifyOutcome],
    before_score: float,
    max_attempts: int = 3,
) -> FixLoopResult:
    """propose(attempt_number, previous_attempt_result) -> FixProposal;
    verify(proposal) -> VerifyOutcome. Hard-caps at max_attempts (default
    3, per AGENT_SPECS.md §3's "Retry policy: max 3 attempts") — after the
    cap, returns resolved=False with every attempt's full history, never
    raises and never retries silently past it (AGENTS.md #1's "diagnosed
    but not auto-fixed" is a legitimate, expected outcome here)."""
    attempts: list[FixAttemptRecord] = []
    previous_attempt_result: PreviousAttemptResult | None = None

    for attempt_number in range(1, max_attempts + 1):
        proposal = propose(attempt_number, previous_attempt_result)
        outcome = verify(proposal)
        attempts.append(FixAttemptRecord(
            attempt=attempt_number, patch=proposal.patch, rationale=proposal.rationale,
            score_after=outcome.score_after, resolved=outcome.resolved,
        ))
        if outcome.resolved:
            return FixLoopResult(
                attempts=attempts, resolved=True, final_patch=proposal.patch,
                before_score=before_score, after_score=outcome.score_after,
            )
        previous_attempt_result = PreviousAttemptResult(
            patch_applied=proposal.patch, score_after_fix=outcome.score_after, still_regressed=True,
        )

    return FixLoopResult(
        attempts=attempts, resolved=False,
        final_patch=attempts[-1].patch if attempts else None,
        before_score=before_score,
        after_score=attempts[-1].score_after if attempts else None,
    )


# ---------------------------------------------------------------------------
# Layer B — real Nemotron wiring. `verify` is STILL injected, by design —
# see the module docstring above for why.
# ---------------------------------------------------------------------------

def propose_patch(
    *,
    job_id: str,
    diagnosis: dict,
    full_file_contents: str,
    attempt_number: int,
    previous_attempt_result: PreviousAttemptResult | None = None,
    temperature: float = FIXER_TEMPERATURE,
) -> FixProposal:
    """One real Nemotron Ultra call, per AGENT_SPECS.md §3's input schema.
    `diagnosis` should be a plain dict (e.g. dataclasses.asdict() of
    diagnoser.DiagnosisResult) — see module docstring's calling-convention
    note."""
    payload = {
        "diagnosis": diagnosis,
        "full_file_contents": full_file_contents,
        "attempt_number": attempt_number,
        "previous_attempt_result": (
            None if previous_attempt_result is None else {
                "patch_applied": previous_attempt_result.patch_applied,
                "score_after_fix": previous_attempt_result.score_after_fix,
                "still_regressed": previous_attempt_result.still_regressed,
            }
        ),
    }
    result = call_nemotron(
        job_id=job_id, step=f"fix:attempt={attempt_number}", model="ultra",
        system_prompt=FIXER_SYSTEM_PROMPT, payload=payload, temperature=temperature,
    )
    proposal = _validate_schema(result.parsed, attempt_number=attempt_number)
    log.info(
        "fixer.proposal", job_id=job_id, attempt=attempt_number,
        confidence_will_resolve=proposal.confidence_will_resolve,
    )
    return proposal


def run_fix_loop_live(
    *,
    job_id: str,
    diagnosis: dict,
    full_file_contents: str,
    verify: Callable[[FixProposal], VerifyOutcome],
    before_score: float,
    max_attempts: int = 3,
    temperature: float = FIXER_TEMPERATURE,
) -> FixLoopResult:
    """The real thing: wires run_fix_loop's `propose` slot to real
    Nemotron calls via propose_patch(). `verify` is still the caller's to
    supply — api.py's `_run_analysis()` builds the real sandbox-backed
    verify closure and passes it in here, per the module docstring above."""
    log.info("fixer.start", job_id=job_id, max_attempts=max_attempts, before_score=before_score)

    def _propose(attempt_number: int, previous_attempt_result: PreviousAttemptResult | None) -> FixProposal:
        return propose_patch(
            job_id=job_id, diagnosis=diagnosis, full_file_contents=full_file_contents,
            attempt_number=attempt_number, previous_attempt_result=previous_attempt_result,
            temperature=temperature,
        )

    result = run_fix_loop(propose=_propose, verify=verify, before_score=before_score, max_attempts=max_attempts)
    log.info(
        "fixer.result", job_id=job_id, resolved=result.resolved,
        attempts=len(result.attempts), after_score=result.after_score,
    )
    return result
