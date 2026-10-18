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

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Literal

import structlog

from events import JobCancelled, Reporter, or_null
from models import call_nemotron
from patcher import PatchBuildError, edits_to_patch

log = structlog.get_logger("fixer")

Confidence = Literal["high", "medium", "low"]
_CONFIDENCE_LEVELS: frozenset[str] = frozenset({"high", "medium", "low"})

FIXER_TEMPERATURE = 0.2  # within AGENTS.md #4's documented 0.1-0.3 for the Fixer

# Verbatim from AGENT_SPECS.md §3 / BUILD_02 §2 — don't edit here without
# updating that doc first (AGENTS.md #3).
FIXER_SYSTEM_PROMPT = """You are a senior engineer fixing a diagnosed performance regression.
You will be given the diagnosis (root cause + cited lines), the full
contents of the affected file(s) — each introduced by a "# FILE: <path>"
line — and, on retries, the result of your previous attempt (if that
attempt could not be applied or crashed when run, its note says why).

Propose a minimal fix that addresses the specific root cause. Do not make
unrelated changes. If this is a retry and your previous attempt did not
resolve the regression, explain what you're changing about your approach
before proposing the new fix.

Express the fix as EDITS, not as a diff. Each edit has the file path, an
"old" string copied EXACTLY (character for character, including
indentation and line breaks) from that file, and the "new" string that
replaces it. Each "old" must appear exactly once in its file, so include
enough surrounding lines to make it unique. To delete code, use "" as
"new".

Return ONLY JSON matching this schema:
{
  "edits": [{"file": "<path>", "old": "<exact text>", "new": "<replacement>"}],
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
    # Set by the loop just before verify(), so a verifier running several
    # candidates at once can tell them apart without shared counters.
    attempt: int = 0


@dataclass
class PreviousAttemptResult:
    """Mirrors AGENT_SPECS.md §3's previous_attempt_result input shape
    exactly, so it serializes straight into the next call's payload."""
    patch_applied: str
    score_after_fix: float
    still_regressed: bool
    # Additive to AGENT_SPECS.md §3: why the attempt failed when it never got a
    # score (the patched code crashed, timed out, or printed no number). Only
    # sent to the model when non-empty, so the documented payload is unchanged
    # in the ordinary case.
    note: str = ""


@dataclass
class VerifyOutcome:
    """What an injected `verify` callable must return. `resolved` is the
    ONLY source of truth for whether a fix worked — see module docstring's
    "never marks itself as verified.\""""
    score_after: float
    resolved: bool
    note: str = ""  # set when the attempt crashed / didn't apply and so has no measured score
    # --- correctness guard (all optional; unset when the job has no test command) ---
    perf_ok: bool | None = None  # benchmark came back within threshold (independent of the tests)
    tests_passed: bool | None = None  # None = tests were not run (guard off, or perf already failed)
    tests_note: str = ""  # why the tests rejected the patch; fed back to the model on retry
    test_output: str = ""  # tail of the test run, kept for the UI


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
    error: str = ""  # non-empty ⇒ score_after is NOT a measurement (the patched run crashed / didn't apply)
    tests_passed: bool | None = None  # correctness guard verdict; None = not run
    test_output: str = ""
    # Best-of-N (additive): which round/candidate this was, and whether it won.
    # 0 = not part of a best-of-N run (the sequential loop leaves these unset).
    round: int = 0
    candidate: int = 0
    selected: bool = False


@dataclass
class FixLoopResult:
    attempts: list[FixAttemptRecord]
    resolved: bool
    final_patch: str | None  # the patch reported (see run_fix_rounds for how it's chosen) — None only if attempts is empty
    before_score: float
    after_score: float | None  # that patch's score_after — None only if attempts is empty
    selected_attempt: int | None = None  # attempt number of the winner (resolved runs only)


def patch_stats(patch: str) -> dict[str, int]:
    """Added/removed line counts of a unified diff (file headers excluded)."""
    added = sum(1 for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in patch.splitlines() if l.startswith("-") and not l.startswith("---"))
    files = sum(1 for l in patch.splitlines() if l.startswith("+++ "))
    return {"added": added, "removed": removed, "files": files}


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
    on_attempt: Callable[[FixAttemptRecord], None] | None = None,
    reporter: Reporter | None = None,
) -> FixLoopResult:
    """propose(attempt_number, previous_attempt_result) -> FixProposal;
    verify(proposal) -> VerifyOutcome. Hard-caps at max_attempts (default
    3, per AGENT_SPECS.md §3's "Retry policy: max 3 attempts") — after the
    cap, returns resolved=False with every attempt's full history, never
    raises and never retries silently past it (AGENTS.md #1's "diagnosed
    but not auto-fixed" is a legitimate, expected outcome here)."""
    rep = or_null(reporter)
    attempts: list[FixAttemptRecord] = []
    previous_attempt_result: PreviousAttemptResult | None = None

    for attempt_number in range(1, max_attempts + 1):
        rep.check_cancelled()
        span = rep.start("fix", "fix", f"fix attempt {attempt_number}/{max_attempts}", attempt=attempt_number,
                         max_attempts=max_attempts, retry=previous_attempt_result is not None)
        proposal = propose(attempt_number, previous_attempt_result)
        proposal.attempt = attempt_number
        stats = patch_stats(proposal.patch)
        rep.emit(
            "patch.built", "fix",
            f"patch proposed: +{stats['added']} −{stats['removed']} across {stats['files']} file(s) — {proposal.rationale}",
            attempt=attempt_number, patch=proposal.patch, rationale=proposal.rationale,
            confidence=proposal.confidence_will_resolve, **stats,
        )
        outcome = verify(proposal)
        record = FixAttemptRecord(
            attempt=attempt_number, patch=proposal.patch, rationale=proposal.rationale,
            score_after=outcome.score_after, resolved=outcome.resolved, error=outcome.note,
            tests_passed=outcome.tests_passed, test_output=outcome.test_output,
        )
        attempts.append(record)
        rep.end(
            span,
            (f"attempt {attempt_number} VERIFIED — benchmark back to {outcome.score_after:.3f} ms"
             + (" and the test suite passes" if outcome.tests_passed else "") if outcome.resolved
             else f"attempt {attempt_number} rejected — benchmark is {outcome.score_after:.3f} ms but the test suite FAILED"
             if outcome.tests_passed is False
             else f"attempt {attempt_number} failed — the patched code did not run cleanly (no score)" if outcome.note
             else f"attempt {attempt_number} did not resolve it — {outcome.score_after:.3f} ms"),
            attempt=attempt_number, score_after=outcome.score_after, resolved=outcome.resolved,
            before_score=before_score,
            # non-empty ⇒ score_after is a placeholder, not a measurement
            error=outcome.note or None,
            **({"tests_passed": outcome.tests_passed} if outcome.tests_passed is not None else {}),
        )
        if on_attempt:
            on_attempt(record)
        if outcome.resolved:
            return FixLoopResult(
                attempts=attempts, resolved=True, final_patch=proposal.patch,
                before_score=before_score, after_score=outcome.score_after, selected_attempt=attempt_number,
            )
        previous_attempt_result = PreviousAttemptResult(
            patch_applied=proposal.patch, score_after_fix=outcome.score_after,
            # Tests can reject a patch that DID fix the benchmark — say so, so the
            # model doesn't discard the speed-up and start over from scratch.
            still_regressed=True if outcome.perf_ok is None else not outcome.perf_ok,
            note=outcome.note or outcome.tests_note,
        )

    return FixLoopResult(
        attempts=attempts, resolved=False,
        final_patch=attempts[-1].patch if attempts else None,
        before_score=before_score,
        after_score=attempts[-1].score_after if attempts else None,
    )


# ---------------------------------------------------------------------------
# Layer A+ — best-of-N rounds. Same contract and honesty rules as run_fix_loop,
# but each round asks for several DIFFERENT fixes at once and keeps the best one
# that actually verified. With candidates <= 1 this IS run_fix_loop.
# ---------------------------------------------------------------------------

MAX_CANDIDATES = 5

# One approach per candidate, so the candidates are genuinely different ideas
# rather than N samples of the same one. Candidate 1 is always the plain,
# minimal fix — the same thing a single-candidate run would have tried.
APPROACHES: tuple[str, ...] = (
    "Make the smallest, most surgical change that removes the root cause.",
    "Restore the behaviour of the code from before the regression — undo what the guilty change did "
    "wrong — while keeping any part of that change that was an unrelated, intended improvement.",
    "Take a different route from the obvious fix: batch the repeated work, cache or memoize it, or use a "
    "better data structure or algorithm — keeping the program's behaviour identical.",
    "Fix it where the problem originates, even if that means a slightly broader change, so that "
    "other callers cannot hit the same slowdown.",
    "Prefer whichever fix is easiest to review and least likely to change behaviour, even if it is not "
    "the fastest conceivable one.",
)

FIXER_APPROACH_ADDENDUM = """

You are one of several engineers working on this same regression IN PARALLEL;
each of you is given a different "approach" in the input. Follow yours. Every
candidate is benchmarked, and only the one that verifies best is kept, so a
distinct, correct idea is worth more than a copy of the obvious fix."""


def _patch_size(patch: str) -> int:
    st = patch_stats(patch)
    return st["added"] + st["removed"]


def _closest(attempts: list[FixAttemptRecord]) -> FixAttemptRecord | None:
    """The attempt that came nearest to working: one that ran and was fast but
    failed the tests beats one that was merely measured, which beats a crash."""
    ran = [a for a in attempts if not a.error]
    if not ran:
        return attempts[-1] if attempts else None
    return min(ran, key=lambda a: (0 if a.tests_passed is False else 1, a.score_after, _patch_size(a.patch)))


def run_fix_rounds(
    *,
    propose: Callable[[int, int, PreviousAttemptResult | None], FixProposal],
    verify: Callable[[FixProposal], VerifyOutcome],
    before_score: float,
    candidates: int = 3,
    max_rounds: int = 3,
    verify_parallelism: int = 1,
    on_attempt: Callable[[FixAttemptRecord], None] | None = None,
    reporter: Reporter | None = None,
) -> FixLoopResult:
    """propose(round, candidate, previous) -> FixProposal (called concurrently —
    it must be thread-safe); verify(proposal) -> VerifyOutcome (called with at
    most `verify_parallelism` at once: 1 where concurrent benchmarks would share
    a CPU, more where each candidate gets its own isolated VM).

    Each round: propose `candidates` fixes at once, verify them, and if any
    VERIFIED keep the one with the best measured score (ties -> the smaller
    patch). `resolved` is still only ever what `verify` said — choosing between
    verified fixes never turns an unverified one into a verified one. If no
    candidate verified, the next round gets feedback from the closest miss.
    At most max_rounds rounds. A candidate whose proposal couldn't be produced
    is skipped; only if EVERY proposal in a round fails does the error surface.
    """
    k = max(1, min(int(candidates), MAX_CANDIDATES))
    if k == 1:
        return run_fix_loop(
            propose=lambda n, prev: propose(n, 1, prev), verify=verify, before_score=before_score,
            max_attempts=max_rounds, on_attempt=on_attempt, reporter=reporter,
        )

    rep = or_null(reporter)
    attempts: list[FixAttemptRecord] = []
    previous: PreviousAttemptResult | None = None
    counter = 0
    workers = max(1, min(int(verify_parallelism), k))

    for rnd in range(1, max_rounds + 1):
        rep.check_cancelled()
        rspan = rep.start(
            "fixround", "fix",
            f"fix round {rnd}/{max_rounds} · {k} different fixes proposed at once"
            + (f", verified {workers} at a time" if workers > 1 else ", verified one after another"),
            round=rnd, max_rounds=max_rounds, candidates=k, verify_parallelism=workers, retry=previous is not None,
        )

        # -- propose all candidates concurrently (model calls only: no timing at stake)
        prev_for_round = previous

        def _propose(c: int):
            try:
                return propose(rnd, c, prev_for_round)
            except BaseException as e:  # noqa: BLE001 — settled below, after the round's other proposals finish
                return e

        with ThreadPoolExecutor(max_workers=k, thread_name_prefix="culprit-fix") as pool:
            proposals = list(pool.map(_propose, range(1, k + 1)))

        failures = [e for e in proposals if isinstance(e, BaseException)]
        for e in failures:
            if isinstance(e, JobCancelled):
                raise e
        if len(failures) == k:
            rep.end(rspan, f"round {rnd}: none of the {k} proposals could be produced", round=rnd, produced=0)
            raise failures[0]
        for c, p in enumerate(proposals, start=1):
            if isinstance(p, BaseException):
                rep.emit("fix.candidate_failed", "fix",
                         f"candidate {c} could not be produced ({type(p).__name__}: {str(p)[:200]}) — carrying on with the others",
                         round=rnd, candidate=c, error=str(p)[:400])

        # -- announce + number the candidates in a stable order, then verify
        jobs: list[tuple[int, int, FixProposal, str]] = []
        for c, p in enumerate(proposals, start=1):
            if isinstance(p, BaseException):
                continue
            counter += 1
            p.attempt = counter
            span = rep.start("fix", "fix", f"fix attempt {counter} (round {rnd}, candidate {c}/{k})",
                             attempt=counter, round=rnd, candidate=c, candidates=k,
                             max_attempts=max_rounds * k, retry=prev_for_round is not None)
            stats = patch_stats(p.patch)
            rep.emit(
                "patch.built", "fix",
                f"candidate {c}: +{stats['added']} −{stats['removed']} across {stats['files']} file(s) — {p.rationale}",
                attempt=counter, round=rnd, candidate=c, patch=p.patch, rationale=p.rationale,
                confidence=p.confidence_will_resolve, **stats,
            )
            jobs.append((c, counter, p, span))

        def _verify(job: tuple[int, int, FixProposal, str]):
            c, n, p, span = job
            try:
                rep.check_cancelled()
                out = verify(p)
            except BaseException as e:  # noqa: BLE001 — a real failure; raised below once the round settles
                return e
            rec = FixAttemptRecord(
                attempt=n, patch=p.patch, rationale=p.rationale, score_after=out.score_after, resolved=out.resolved,
                error=out.note, tests_passed=out.tests_passed, test_output=out.test_output, round=rnd, candidate=c,
            )
            rep.end(
                span,
                (f"attempt {n} VERIFIED — {out.score_after:.3f} ms" + (" and the test suite passes" if out.tests_passed else "")
                 if out.resolved
                 else f"attempt {n} rejected — {out.score_after:.3f} ms but the test suite FAILED" if out.tests_passed is False
                 else f"attempt {n} failed — the patched code did not run cleanly (no score)" if out.note
                 else f"attempt {n} did not resolve it — {out.score_after:.3f} ms"),
                attempt=n, round=rnd, candidate=c, score_after=out.score_after, resolved=out.resolved,
                before_score=before_score, error=out.note or None,
                **({"tests_passed": out.tests_passed} if out.tests_passed is not None else {}),
            )
            return rec, out

        if workers > 1 and len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="culprit-verify") as pool:
                results = list(pool.map(_verify, jobs))
        else:
            results = [_verify(j) for j in jobs]

        errs = [r for r in results if isinstance(r, BaseException)]
        if errs:
            rep.end(rspan, f"round {rnd} stopped: {type(errs[0]).__name__}", round=rnd)
            raise errs[0]
        records = [rec for rec, _ in results]
        outcomes = {rec.attempt: out for rec, out in results}

        winners = [r for r in records if r.resolved]
        winner = min(winners, key=lambda r: (r.score_after, _patch_size(r.patch), r.attempt)) if winners else None
        if winner is not None:
            winner.selected = True
        attempts.extend(records)

        # -- report: losers first, the winner last (so the "latest" state is the winner)
        if on_attempt:
            for r in [x for x in records if x is not winner] + ([winner] if winner else []):
                on_attempt(r)

        if winner is not None:
            ranked = ", ".join(f"#{r.candidate} {r.score_after:.3f} ms" for r in sorted(winners, key=lambda r: r.score_after))
            rep.emit(
                "fix.selected", "fix",
                f"picked candidate {winner.candidate} — best of {len(winners)} verified ({ranked})"
                if len(winners) > 1 else f"candidate {winner.candidate} was the only one to verify ({winner.score_after:.3f} ms)",
                round=rnd, attempt=winner.attempt, candidate=winner.candidate, verified=len(winners), of=len(records),
                score_after=winner.score_after,
            )
            rep.end(rspan, f"round {rnd}: {len(winners)} of {len(records)} verified — kept candidate {winner.candidate}",
                    round=rnd, verified=len(winners), of=len(records), selected=winner.attempt)
            return FixLoopResult(
                attempts=attempts, resolved=True, final_patch=winner.patch,
                before_score=before_score, after_score=winner.score_after, selected_attempt=winner.attempt,
            )

        rep.end(rspan, f"round {rnd}: none of the {len(records)} candidates verified", round=rnd, verified=0, of=len(records))
        best = _closest(records)
        if best is not None:
            bo = outcomes[best.attempt]
            previous = PreviousAttemptResult(
                patch_applied=best.patch, score_after_fix=best.score_after,
                still_regressed=True if bo.perf_ok is None else not bo.perf_ok,
                note=bo.note or bo.tests_note,
            )

    best = _closest(attempts)
    return FixLoopResult(
        attempts=attempts, resolved=False,
        final_patch=best.patch if best else None,
        before_score=before_score,
        after_score=best.score_after if best else None,
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
    reporter: Reporter | None = None,
    approach: str | None = None,
    candidate: int | None = None,
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
                **({"note": previous_attempt_result.note} if previous_attempt_result.note else {}),
            }
        ),
    }
    extra = {"reporter": reporter} if reporter is not None else {}
    system_prompt = FIXER_SYSTEM_PROMPT
    if approach:  # best-of-N only; a single-candidate call is byte-for-byte what it always was
        payload["approach"] = approach
        system_prompt = FIXER_SYSTEM_PROMPT + FIXER_APPROACH_ADDENDUM
    result = call_nemotron(
        job_id=job_id,
        step=f"fix:attempt={attempt_number}" + (f":candidate={candidate}" if candidate else ""), model="ultra",
        system_prompt=system_prompt, payload=payload, temperature=temperature, **extra,
    )
    parsed = dict(result.parsed)
    if "edits" in parsed and "patch" not in parsed:
        # Model gave exact-text edits; build the unified diff deterministically
        # (LLM-written diffs are rejected by `git apply` far too often).
        try:
            parsed["patch"] = edits_to_patch(full_file_contents, parsed["edits"])
        except PatchBuildError as e:
            # A bad edit is a failed attempt, not a dead job: the note below
            # becomes the "patch", fails to apply in the sandbox, and is fed
            # back to the model as previous_attempt_result.patch_applied.
            log.warning("fixer.edit_did_not_match", job_id=job_id, attempt=attempt_number, error=str(e))
            or_null(reporter).emit(
                "patch.rejected", "fix",
                f"Ultra's edit didn't match the file text — feeding the error back: {e}",
                attempt=attempt_number, error=str(e),
            )
            parsed["patch"] = f"# EDIT NOT APPLIED — {e}\n"
    proposal = _validate_schema(parsed, attempt_number=attempt_number)
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
    on_attempt: Callable[[FixAttemptRecord], None] | None = None,
    reporter: Reporter | None = None,
    candidates: int = 1,
    verify_parallelism: int = 1,
) -> FixLoopResult:
    """The real thing: wires run_fix_loop's `propose` slot to real
    Nemotron calls via propose_patch(). `verify` is still the caller's to
    supply — api.py's `_run_analysis()` builds the real sandbox-backed
    verify closure and passes it in here, per the module docstring above.

    candidates > 1 switches to best-of-N rounds (run_fix_rounds): `max_attempts`
    then counts ROUNDS, each proposing that many different fixes (one approach
    hint apiece) and keeping the best verified one. candidates=1 is the
    original sequential loop, unchanged."""
    log.info("fixer.start", job_id=job_id, max_attempts=max_attempts, before_score=before_score,
             candidates=candidates)

    if candidates > 1:
        k = min(int(candidates), MAX_CANDIDATES)

        def _propose_candidate(rnd: int, c: int, previous: PreviousAttemptResult | None) -> FixProposal:
            extra = {"reporter": reporter} if reporter is not None else {}
            return propose_patch(
                job_id=job_id, diagnosis=diagnosis, full_file_contents=full_file_contents,
                attempt_number=rnd, previous_attempt_result=previous, temperature=temperature,
                approach=APPROACHES[(c - 1) % len(APPROACHES)], candidate=c, **extra,
            )

        result = run_fix_rounds(
            propose=_propose_candidate, verify=verify, before_score=before_score, candidates=k,
            max_rounds=max_attempts, verify_parallelism=verify_parallelism, on_attempt=on_attempt, reporter=reporter,
        )
        log.info("fixer.result", job_id=job_id, resolved=result.resolved, attempts=len(result.attempts),
                 after_score=result.after_score, selected=result.selected_attempt)
        return result

    def _propose(attempt_number: int, previous_attempt_result: PreviousAttemptResult | None) -> FixProposal:
        extra = {"reporter": reporter} if reporter is not None else {}
        return propose_patch(
            job_id=job_id, diagnosis=diagnosis, full_file_contents=full_file_contents,
            attempt_number=attempt_number, previous_attempt_result=previous_attempt_result,
            temperature=temperature, **extra,
        )

    result = run_fix_loop(
        propose=_propose, verify=verify, before_score=before_score, max_attempts=max_attempts,
        on_attempt=on_attempt, reporter=reporter,
    )
    log.info(
        "fixer.result", job_id=job_id, resolved=result.resolved,
        attempts=len(result.attempts), after_score=result.after_score,
    )
    return result
