# backend/guardtest.py
"""
guardtest.py — a regression-proof test for the fix.

A verified fix answers "is it fast again?". It does not answer "will it stay
fast?" — the next refactor can bring the N+1 straight back, and nothing would
notice until a user did. So after a fix is verified, Nemotron 3 Ultra writes a
small `unittest` that pins the *root cause* (how many times a function is
called, how many queries are issued…) — deliberately never wall-clock time,
which is flaky — and Culprit PROVES the test is any good before offering it:

  1. on the regressed commit the test must FAIL, with an assertion (not crash);
  2. on the regressed commit plus the verified fix it must PASS;
  3. (evidence only) it should also pass on the last good commit, which shows
     it encodes the intended behaviour rather than the fix's exact shape.

A test that can't clear 1 and 2 is thrown away and reported as "not verified" —
never shipped. This whole stage is best-effort extra evidence: it can't fail a
job or change a job's result (see api.py).

Split like bisector.py / fixer.py:
  Layer A — pure, injectable: source checks, unittest-output reading and
            run_guard_loop() (propose / run callables are parameters).
  Layer B — the real Ultra call: propose_guard_test().
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

import structlog

from events import Reporter
from models import call_nemotron

log = structlog.get_logger("guardtest")

GUARD_TEST_FILE = "test_culprit_regression_guard.py"
GUARD_TEST_COMMAND = f"python -m unittest {GUARD_TEST_FILE[:-3]} -v"
MAX_TEST_CHARS = 20_000
MAX_BENCH_SOURCE_CHARS = 8_000
GUARD_TEMPERATURE = 0.2
MAX_ATTEMPTS = 2

GUARD_SYSTEM_PROMPT = """You are a senior engineer writing a REGRESSION GUARD TEST for a performance
regression that has just been diagnosed and fixed. The test will be added to
the repository so the regression can never silently come back.

You are given: the diagnosis (root cause + cited lines), the verified fix (a
unified diff), the contents of the affected files (each introduced by a
"# FILE: <path>" line), the benchmark command and, when available, the
benchmark's source, and, when available, profile hotspots measured on the
last good commit vs. the regressed one (call counts). On a retry you also get
what was wrong with your previous test.

Write ONE Python `unittest` module that:
1. FAILS with an AssertionError on the code WITH the regression, and PASSES on
   the code with the fix applied. A test that errors (ImportError, TypeError,
   NameError...) instead of failing an assertion is useless.
2. Pins a deterministic, structural property of the ROOT CAUSE — e.g. how many
   times a function is called, how many queries/requests are issued, that an
   expensive result is computed once — NEVER wall-clock time. Do not use
   time, perf_counter, timeit or sleep-based thresholds: they are flaky and
   will be rejected.
3. Tests behaviour, not text: do not read or inspect source code
   (inspect.getsource, ast, open() on .py files).
4. Uses only the standard library and the repository's own modules, no
   network, and finishes in well under 20 seconds. Good tools: wrap or patch
   the function with unittest.mock (patch.object(..., wraps=original)) and
   count calls, or count calls to a named function with sys.setprofile while
   running the same code path the benchmark runs (e.g. runpy.run_path on the
   benchmark script, or import and call what it calls).
5. Chooses its threshold from the evidence (call counts before vs. after),
   leaving a comfortable margin on both sides, and explains it in a comment.

The module is saved at the repository root as the given "test_file_name" and
run with `python -m unittest`. Imports must work from the repository root.
If you cannot write a test that meets these rules, return "test_code": "" and
say why in "asserts".

Return ONLY JSON matching this schema:
{
  "test_code": "<the complete Python source of the test module>",
  "asserts": "<one sentence: the exact property this test pins>"
}"""

# Things that make a regression test brittle or meaningless, rejected up front
# (and the reason is fed back to the model).
_FORBIDDEN = (
    (re.compile(r"\b(perf_counter|monotonic|process_time|timeit)\b|\btime\.time\s*\("),
     "it measures wall-clock time — assert on a deterministic COUNT instead (calls, queries, requests)"),
    (re.compile(r"\binspect\.getsource|\bgetsourcelines\b|\bast\.parse\b"),
     "it inspects source text — test the code's behaviour, not how it is written"),
)


class GuardTestError(Exception):
    """The proposal was unusable in a way the model can't recover from."""


@dataclass
class GuardProposal:
    test_code: str
    asserts: str


@dataclass
class SuiteVerdict:
    """What a `unittest` run's output says (exit code alone can't tell a failed
    assertion from a crash)."""
    ran: int
    failures: int
    errors: int
    ok: bool


@dataclass
class GuardAttempt:
    attempt: int
    status: str  # "verified" | "rejected" | "did_not_fail" | "crashed" | "timed_out" | "failed_with_fix" | "infra" | "declined"
    note: str = ""
    test_code: str = ""


@dataclass
class GuardResult:
    verified: bool
    test_code: str | None = None
    asserts: str = ""
    reason: str = ""  # why there is no verified test (empty when verified)
    attempts: list[GuardAttempt] = field(default_factory=list)
    fails_without_fix: bool | None = None
    passes_with_fix: bool | None = None
    passes_on_good: bool | None = None  # evidence only; None = not checked / couldn't run


# ---------------------------------------------------------------------------
# Layer A — pure helpers
# ---------------------------------------------------------------------------

def check_test_source(code: str) -> str | None:
    """None if the source is acceptable, else a model-readable reason."""
    if not code or not code.strip():
        return "the test is empty"
    if len(code) > MAX_TEST_CHARS:
        return f"the test is too long ({len(code)} chars; the limit is {MAX_TEST_CHARS}) — keep it small and focused"
    try:
        compile(code, GUARD_TEST_FILE, "exec")
    except SyntaxError as e:
        return f"the test is not valid Python: {e.msg} (line {e.lineno})"
    if "unittest" not in code or "TestCase" not in code:
        return "the test must be a unittest.TestCase module (it is run with `python -m unittest`)"
    for rx, why in _FORBIDDEN:
        if rx.search(code):
            return why
    return None


_RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.M)
_FAILED_RE = re.compile(r"^FAILED \(([^)]*)\)", re.M)


def read_unittest_output(output: str, *, exit_code: int | None) -> SuiteVerdict:
    """Parse the tail of a `python -m unittest` run. Unknown/garbled output
    reads as ran=0, which every caller treats as 'did not run a test'."""
    m = _RAN_RE.search(output or "")
    ran = int(m.group(1)) if m else 0
    failures = errors = 0
    f = _FAILED_RE.search(output or "")
    if f:
        for part in f.group(1).split(","):
            k, _, v = part.strip().partition("=")
            if v.isdigit():
                if k == "failures":
                    failures = int(v)
                elif k == "errors":
                    errors = int(v)
    ok = exit_code == 0 and ran > 0 and not f
    return SuiteVerdict(ran=ran, failures=failures, errors=errors, ok=ok)


def benchmark_source(benchmark_command: str, read: Callable[[str], str]) -> str | None:
    """The script the benchmark command runs (`python bench.py ...`), via
    `read(path)`; None when it can't be found. Gives the model the exact code
    path to exercise."""
    for tok in benchmark_command.split():
        if tok.endswith(".py") and not tok.startswith(("/", "-")) and ".." not in tok.split("/"):
            try:
                text = read(tok)
            except Exception:  # noqa: BLE001 — optional context, never an error
                return None
            return text[:MAX_BENCH_SOURCE_CHARS]
    return None


RunSuite = Callable[[str], Any]  # test source -> SandboxClient SuiteResult-like (passed/exit_code/timed_out/output_tail)


def run_guard_loop(
    *,
    propose: Callable[[int, dict | None], GuardProposal],
    run_without_fix: RunSuite,
    run_with_fix: RunSuite,
    run_on_good: RunSuite | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    on_attempt: Callable[[GuardAttempt], None] | None = None,
) -> GuardResult:
    """Propose a test, PROVE it (fails on the regressed code, passes with the
    fix), retry once with feedback. `run_*` execute the test source somewhere
    real — this function decides nothing the runs didn't show.

    A SandboxError from any run is infrastructure, not a verdict about the
    test: the loop stops and says it could not verify (it never guesses).
    """
    from sandbox_client import SandboxError  # local: Layer A stays importable without the sandbox stack

    result = GuardResult(verified=False)
    previous: dict | None = None

    def record(att: GuardAttempt) -> None:
        result.attempts.append(att)
        if on_attempt:
            on_attempt(att)

    for n in range(1, max_attempts + 1):
        prop = propose(n, previous)
        code = prop.test_code or ""
        if not code.strip():
            record(GuardAttempt(n, "declined", prop.asserts or "the model could not write a test that meets the rules"))
            result.reason = f"the model declined: {prop.asserts or 'no reason given'}"
            return result  # asking again for the same thing rarely helps

        problem = check_test_source(code)
        if problem:
            record(GuardAttempt(n, "rejected", problem, code))
            previous = {"problem": f"your test was rejected before running: {problem}", "output": ""}
            result.reason = problem
            continue

        try:
            without = run_without_fix(code)
        except SandboxError as e:
            record(GuardAttempt(n, "infra", str(e)[-300:], code))
            result.reason = f"the sandbox could not run the test: {str(e)[-200:]}"
            return result
        w = read_unittest_output(without.output_tail, exit_code=without.exit_code)
        if without.timed_out:
            note = "the test timed out on the regressed code — make it faster (smaller inputs, fewer iterations)"
            record(GuardAttempt(n, "timed_out", note, code))
            previous, result.reason = {"problem": note, "output": without.output_tail[-1200:]}, note
            continue
        if without.passed or w.ok:
            note = ("the test PASSED on the regressed code, so it would not have caught this regression — "
                    "assert the property that the regression breaks")
            record(GuardAttempt(n, "did_not_fail", note, code))
            previous, result.reason = {"problem": note, "output": without.output_tail[-1200:]}, note
            continue
        if w.errors > 0 or w.ran == 0 or w.failures == 0:
            note = ("the test crashed (an error, not a failed assertion) on the regressed code — fix the test so "
                    "it runs and its ASSERTION fails")
            record(GuardAttempt(n, "crashed", note, code))
            previous, result.reason = {"problem": note, "output": without.output_tail[-1200:]}, note
            continue

        try:
            with_fix = run_with_fix(code)
        except SandboxError as e:
            record(GuardAttempt(n, "infra", str(e)[-300:], code))
            result.reason = f"the sandbox could not run the test with the fix: {str(e)[-200:]}"
            return result
        f = read_unittest_output(with_fix.output_tail, exit_code=with_fix.exit_code)
        if with_fix.timed_out or not (with_fix.passed and f.ok):
            note = "the test FAILED on the code WITH the fix applied — it must pass once the regression is fixed"
            record(GuardAttempt(n, "failed_with_fix", note, code))
            previous, result.reason = {"problem": note, "output": with_fix.output_tail[-1200:]}, note
            continue

        # Proven: red on the regression, green with the fix.
        result.verified, result.test_code, result.asserts, result.reason = True, code, prop.asserts, ""
        result.fails_without_fix = result.passes_with_fix = True
        if run_on_good is not None:
            try:
                good = run_on_good(code)
                result.passes_on_good = bool(good.passed and read_unittest_output(
                    good.output_tail, exit_code=good.exit_code).ok)
            except SandboxError as e:
                log.warning("guardtest.good_commit_unavailable", error=str(e)[-200:])
        record(GuardAttempt(n, "verified", prop.asserts, code))
        return result

    return result


# ---------------------------------------------------------------------------
# Layer B — the real Ultra call
# ---------------------------------------------------------------------------

def propose_guard_test(
    *,
    job_id: str,
    diagnosis: dict,
    fix_patch: str,
    full_file_contents: str,
    benchmark_command: str,
    bench_source: str | None,
    hotspots: list[dict] | None,
    attempt_number: int,
    previous: dict | None = None,
    reporter: Reporter | None = None,
    temperature: float = GUARD_TEMPERATURE,
) -> GuardProposal:
    payload: dict[str, Any] = {
        "test_file_name": GUARD_TEST_FILE,
        "diagnosis": {k: diagnosis.get(k) for k in ("category", "explanation", "cited_lines")},
        "fix_patch": fix_patch,
        "full_file_contents": full_file_contents,
        "benchmark_command": benchmark_command,
        "attempt_number": attempt_number,
        "previous_attempt": previous,
    }
    if bench_source:
        payload["benchmark_source"] = bench_source
    if hotspots:
        payload["profile_hotspots"] = hotspots
    extra = {"reporter": reporter} if reporter is not None else {}
    res = call_nemotron(
        job_id=job_id, step=f"guardtest:attempt={attempt_number}", model="ultra",
        system_prompt=GUARD_SYSTEM_PROMPT, payload=payload, temperature=temperature, **extra,
    )
    parsed = res.parsed
    code, asserts = parsed.get("test_code"), parsed.get("asserts")
    if not isinstance(code, str) or not isinstance(asserts, str):
        raise GuardTestError("guard-test response must have string 'test_code' and 'asserts'")
    return GuardProposal(test_code=code, asserts=asserts.strip())
