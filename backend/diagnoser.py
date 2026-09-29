# FILE: backend/diagnoser.py — place at this path in the Culprit repo
# (replaces the Stack-2 stub of the same name).
"""
diagnoser.py — root-cause classification, calls Nemotron 3 Ultra + Tavily.

Spec: docs/build-prompts/BUILD_02_BACKEND_AGENTS.md §1
Schema: docs/AGENT_SPECS.md §2 / docs/TRD.md §2.2

Owns the cited_lines-verification check the spec calls out explicitly:
"Build this check, don't rely on the model's honesty alone" — after
parsing, every string in cited_lines must verify as an actual line from
the diff. This turns AGENTS.md rule 5 ("the Diagnoser must cite specific
diff lines... a diagnosis with no citation should be classified 'other'")
from a prompt-only hope into an enforced invariant:

  1st call  -> if citations verify (or category == "other", which is
               exempt from citing anything — AGENT_SPECS.md §2 only asks
               "other" to *explain why* in the explanation field, not to
               cite lines), done.
  citation mismatch -> exactly ONE re-prompt, with an explicit note that
               the previous citation didn't match the diff (mirrors
               models.py's own malformed-JSON retry nudge, at the
               application level instead of the JSON-parsing level).
  still mismatched  -> this module downgrades the result to
               category="other" ITSELF rather than passing along an
               uncited claim. Never raises for this case — an honest
               "other" is the documented, legitimate outcome.

Only a genuinely schema-invalid response (missing field, unknown
category/confidence, wrong types) raises DiagnoserError — that's the
"can't trust anything in this response" case, distinct from "the specific
citation claim didn't verify," which has a defined, non-exceptional
recovery path above.

Temperature judgment call (flagged — models.py's own docstring says this
exact tension is this module's to resolve): AGENT_SPECS.md §0 says
0.3-0.5 is "fine for Diagnoser explanations if you want more natural
phrasing, but keep the classification field itself deterministic," yet
one call produces both fields at one temperature. DIAGNOSER_TEMPERATURE
below defaults to 0.3 — the low end of that explanation-phrasing range —
as the resolution: still within Ultra's documented 0.1-0.5 bound, biased
toward classification stability over phrasing variety.

Tavily grounding: per AGENT_SPECS.md §2 / BUILD_02 §1, this runs only when
a real category was assigned (never for "other" — there's no pattern to
ground). A failed/empty Tavily search never fails the diagnosis; see
tavily_client.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import structlog

from models import call_nemotron
from tavily_client import TavilyRef, search_grounding

log = structlog.get_logger("diagnoser")

Category = Literal[
    "n_plus_one", "lost_cache", "algorithmic_complexity",
    "blocking_call", "allocation_overhead", "other",
]
Confidence = Literal["high", "medium", "low"]

_CATEGORIES: frozenset[str] = frozenset(
    {"n_plus_one", "lost_cache", "algorithmic_complexity", "blocking_call", "allocation_overhead", "other"}
)
_CONFIDENCE_LEVELS: frozenset[str] = frozenset({"high", "medium", "low"})

DIAGNOSER_TEMPERATURE = 0.3  # see module docstring's temperature judgment call

# Verbatim from AGENT_SPECS.md §2 / BUILD_02 §1 — "don't rephrase this, the
# specific instruction to cite exact lines is load-bearing." Don't edit
# here without updating that doc first (AGENTS.md #3).
DIAGNOSER_SYSTEM_PROMPT = """You are a senior performance engineer diagnosing why a code change slowed
down a benchmark. You will be given the diff that caused the regression,
surrounding file context, the commit message, and before/after benchmark
scores.

Classify the root cause into exactly one of these categories:
- "n_plus_one": repeated DB/network calls introduced inside a loop
- "lost_cache": a cache was removed, invalidated, or bypassed
- "algorithmic_complexity": complexity increased (e.g. O(n) to O(n^2))
- "blocking_call": a new synchronous/blocking operation on a hot path
- "allocation_overhead": unnecessary object allocation or serialization
- "other": if none of the above cleanly fit — explain why in the field below

You MUST cite the exact line(s) from the diff responsible. Do not give a
generic explanation that could apply to any diff — if you cannot point to
specific lines, use category "other" and say so honestly.

Return ONLY JSON matching this schema:
{
  "category": "<one of the categories above>",
  "explanation": "<plain-English explanation, 2-4 sentences, referencing the cited lines>",
  "cited_lines": ["<exact line(s) or short snippet from the diff>"],
  "confidence": "high" | "medium" | "low"
}"""

# Appended (not merged into the constant above) only on the citation retry —
# same pattern as models.py's own _RETRY_NUDGE, one level up the stack.
CITATION_RETRY_ADDENDUM = """Your previous response's cited_lines did not verify: none of them matched
an actual line in the diff you were given. Every entry in cited_lines must
be an exact line (or exact substring of a line) taken verbatim from the
diff — not a paraphrase or a reconstruction from memory. The input now
includes previous_response_with_unverified_citation showing what you
returned last time. If you genuinely cannot point to specific lines that
support a confident category, return category "other" instead and explain
why in the explanation field — that is an honest, acceptable answer."""


class DiagnoserError(Exception):
    """Raised when Nemotron's response is schema-invalid in a way this
    module has no honest recovery path for: a missing field, or a
    category/confidence value outside the documented set. Distinct from a
    citation-verification failure, which IS recoverable (retry once, then
    downgrade to "other" — see module docstring)."""


@dataclass
class DiagnosisResult:
    category: Category
    explanation: str
    cited_lines: list[str]
    confidence: Confidence
    tavily_refs: list[TavilyRef] = field(default_factory=list)
    # True only when THIS module forced category="other" after a citation
    # failed verification twice — an honesty signal distinct from the model
    # voluntarily choosing "other" on its own. Not part of AGENT_SPECS.md's
    # documented output schema; flagged as a useful addition for the
    # dashboard/logs, same spirit as bisector.py's CandidateEvaluation.rounds.
    citation_verification_failed: bool = False
    # The diff this diagnosis was made against — this module already
    # receives it as an input parameter (see diagnose()'s `diff` arg), it
    # was just being discarded before reaching the caller. Threaded through
    # so api.py can hand it to the frontend without a second fetch/read of
    # the same commit (CODE_REVIEW_FINDINGS.md #8 — the frontend's
    # DiagnosisSection/DiffViewer already render this the moment it's
    # present, per frontend/README.md's "Known contract gap" note).
    diff: str = ""


def _validate_schema(parsed: dict, *, guilty_commit_sha: str) -> DiagnosisResult:
    """models.py only guarantees valid JSON, not this schema — a
    syntactically valid but wrong-shaped object must still fail loud here
    (same reasoning as bisector.py's _validate_nano_verdict)."""
    required = {"category", "explanation", "cited_lines", "confidence"}
    missing = required - parsed.keys()
    if missing:
        raise DiagnoserError(f"Diagnoser response for {guilty_commit_sha} missing fields {sorted(missing)}: {parsed!r}")

    category = parsed["category"]
    if category not in _CATEGORIES:
        raise DiagnoserError(f"Diagnoser returned unknown category {category!r} for {guilty_commit_sha}")

    confidence = parsed["confidence"]
    if confidence not in _CONFIDENCE_LEVELS:
        raise DiagnoserError(f"Diagnoser returned unknown confidence {confidence!r} for {guilty_commit_sha}")

    cited_lines = parsed["cited_lines"]
    if not isinstance(cited_lines, list) or not all(isinstance(c, str) for c in cited_lines):
        raise DiagnoserError(f"Diagnoser cited_lines must be a list of strings for {guilty_commit_sha}: {parsed!r}")

    explanation = parsed["explanation"]
    if not isinstance(explanation, str) or not explanation.strip():
        raise DiagnoserError(f"Diagnoser explanation must be a non-empty string for {guilty_commit_sha}: {parsed!r}")

    return DiagnosisResult(
        category=category, explanation=explanation, cited_lines=list(cited_lines), confidence=confidence,
    )


def _normalize_diff_line(line: str) -> str:
    """Strips a single leading unified-diff marker (+/-), not the file
    headers (+++/---) or hunk headers, then strips whitespace. Exists
    because AGENT_SPECS.md's OWN example output drops the diff's leading
    "+" from cited_lines (compare its example diff-derived citation
    against a real unified diff) — a strict raw-substring check would
    reject exactly the citation style the spec's own example models.
    Flagged here as a deliberate tolerance, not a loosening of the check:
    a citation still has to match a real diff line's content, just not
    necessarily including its leading marker character."""
    stripped = line.rstrip("\n")
    if stripped[:1] in ("+", "-") and not stripped.startswith(("+++", "---")):
        stripped = stripped[1:]
    return stripped.strip()


def _verify_cited_lines(cited_lines: list[str], diff: str) -> bool:
    """True only if EVERY entry in cited_lines verifies against diff, as an
    exact match against one of diff's actually-CHANGED lines (added `+` or
    removed `-`, excluding the `+++`/`---` file-header lines) with its
    leading marker stripped (see _normalize_diff_line). Unchanged context
    lines — including a hunk header's trailing function-name text, e.g.
    `@@ -10,6 +10,8 @@ def get_user_orders(user_id):` — are deliberately
    excluded: they're not lines the commit touched, so citing one isn't
    citing "the line responsible" (AGENTS.md rule 5). There is no
    raw-substring-of-the-whole-diff fallback: that used to let a citation
    of any unchanged line in the diff (or an incidental hunk-header
    substring) count as a verified citation of the change itself — see
    CODE_REVIEW_FINDINGS.md #6, which this fixes. An empty cited_lines
    list never verifies: per AGENT_SPECS.md §2, "if you cannot point to
    specific lines... use category 'other'" — a non-'other' category with
    zero citations is exactly the unearned-confidence case this check
    exists to catch."""
    if not cited_lines:
        return False
    changed_lines = (
        l for l in diff.splitlines()
        if l[:1] in ("+", "-") and not l.startswith(("+++", "---"))
    )
    changed_lines = list(changed_lines)
    normalized_diff_lines = {_normalize_diff_line(l) for l in changed_lines}

    # Multi-line statements (e.g. `x = f(\n "..."\n).fetchall()`) are often
    # cited by the model as one logical line. Accept a citation that equals,
    # ignoring whitespace, the concatenation of a run of WHOLE consecutive
    # changed lines from the same hunk position. Still no partial-line matches.
    def _squash(t: str) -> str:
        return "".join(t.split())

    spans: set[str] = set()
    run: list[str] = []
    runs: list[list[str]] = []
    for l in diff.splitlines():
        if l[:1] in ("+", "-") and not l.startswith(("+++", "---")):
            run.append(_squash(_normalize_diff_line(l)))
        else:
            if run:
                runs.append(run)
            run = []
    if run:
        runs.append(run)
    for r in runs:
        for i in range(len(r)):
            acc = ""
            for j in range(i, len(r)):
                acc += r[j]
                spans.add(acc)

    for cited in cited_lines:
        candidate = _normalize_diff_line(cited)
        if not candidate:
            return False
        if candidate in normalized_diff_lines:
            continue
        if _squash(candidate) in spans:
            continue
        return False
    return True


def _downgrade_to_other(diagnosis: DiagnosisResult) -> DiagnosisResult:
    return DiagnosisResult(
        category="other",
        explanation=(
            f"{diagnosis.explanation} [Auto-downgraded from {diagnosis.category!r} to 'other': "
            "cited_lines did not verify as an exact match against the diff, even after one "
            "corrective retry. Reporting this honestly per AGENTS.md rule 5 rather than "
            "passing along an uncited claim.]"
        ),
        cited_lines=diagnosis.cited_lines,
        confidence="low",
        citation_verification_failed=True,
        diff=diagnosis.diff,
    )


def diagnose(
    *,
    job_id: str,
    guilty_commit_sha: str,
    diff: str,
    surrounding_context: str,
    commit_message: str,
    before_score: float,
    after_score: float,
    temperature: float = DIAGNOSER_TEMPERATURE,
    max_tavily_results: int = 3,
) -> DiagnosisResult:
    """The one entry point this module exposes. Raises DiagnoserError only
    for a genuinely schema-invalid Nemotron response; a citation that
    fails to verify is handled internally (retry once, then honest
    downgrade — never raised, never silently passed through)."""
    base_payload = {
        "guilty_commit_sha": guilty_commit_sha,
        "diff": diff,
        "surrounding_context": surrounding_context,
        "commit_message": commit_message,
        "before_score": before_score,
        "after_score": after_score,
    }

    result = call_nemotron(
        job_id=job_id, step="diagnose", model="ultra",
        system_prompt=DIAGNOSER_SYSTEM_PROMPT, payload=base_payload, temperature=temperature,
    )
    diagnosis = _validate_schema(result.parsed, guilty_commit_sha=guilty_commit_sha)
    diagnosis.diff = diff

    if diagnosis.category != "other" and not _verify_cited_lines(diagnosis.cited_lines, diff):
        log.warning(
            "diagnoser.citation_mismatch", job_id=job_id, commit=guilty_commit_sha,
            category=diagnosis.category, cited_lines=diagnosis.cited_lines,
        )
        # Additive extension of AGENT_SPECS.md §2's documented input schema,
        # present only on this retry — flagged for a doc update, same spirit
        # as BUILD_00_OVERVIEW.md's schema-reconciliation flag for job state.
        retry_payload = {
            **base_payload,
            "previous_response_with_unverified_citation": {
                "category": diagnosis.category, "cited_lines": diagnosis.cited_lines,
            },
        }
        result = call_nemotron(
            job_id=job_id, step="diagnose:citation_retry", model="ultra",
            system_prompt=f"{DIAGNOSER_SYSTEM_PROMPT}\n\n{CITATION_RETRY_ADDENDUM}",
            payload=retry_payload, temperature=temperature,
        )
        diagnosis = _validate_schema(result.parsed, guilty_commit_sha=guilty_commit_sha)
        diagnosis.diff = diff

        if diagnosis.category != "other" and not _verify_cited_lines(diagnosis.cited_lines, diff):
            log.error(
                "diagnoser.citation_unverified_after_retry_downgrading", job_id=job_id,
                commit=guilty_commit_sha, original_category=diagnosis.category,
                cited_lines=diagnosis.cited_lines,
            )
            diagnosis = _downgrade_to_other(diagnosis)

    if diagnosis.category != "other":
        diagnosis.tavily_refs = search_grounding(
            diagnosis.category, diagnosis.cited_lines, max_results=max_tavily_results,
        )

    log.info(
        "diagnoser.result", job_id=job_id, commit=guilty_commit_sha, category=diagnosis.category,
        confidence=diagnosis.confidence, citation_verification_failed=diagnosis.citation_verification_failed,
        tavily_refs_found=len(diagnosis.tavily_refs),
    )
    return diagnosis
