# FILE: backend/tests/test_diagnoser.py — place at this path in the
# Culprit repo (new file, sits alongside test_bisector.py etc.).
"""
Follows test_bisector.py's convention: `call_nemotron` and `search_grounding`
are monkeypatched at the diagnoser module level (not via httpx.MockTransport)
since diagnoser.py has no client-injection param of its own — same reasoning
as bisector.bisect_repo's tests.
"""

import pytest

import diagnoser  # noqa: E402  (module import so monkeypatch.setattr works)
from diagnoser import DiagnoserError  # noqa: E402
from models import NemotronResult  # noqa: E402
from tavily_client import TavilyRef  # noqa: E402

REAL_DIFF = """--- a/orders.py
+++ b/orders.py
@@ -10,6 +10,8 @@ def get_user_orders(user_id):
     orders = Order.objects.filter(user_id=user_id)
+    for order in orders:
+        order.line_items = LineItem.objects.filter(order_id=order.id)
     return orders
"""


def _fake_nemotron(parsed: dict):
    calls = []

    def fake(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
        calls.append({"step": step, "payload": payload, "system_prompt": system_prompt})
        return NemotronResult(parsed=parsed, raw_response="{}", attempts=1, model_id="fake-ultra", latency_s=0.0)

    return fake, calls


def _diagnose(monkeypatch, *, diff=REAL_DIFF, tavily_refs=None):
    monkeypatch.setattr(diagnoser, "search_grounding", lambda *a, **k: tavily_refs or [])
    return diagnoser.diagnose(
        job_id="j1", guilty_commit_sha="abc123", diff=diff,
        surrounding_context="", commit_message="perf: batch line items", before_score=100.0, after_score=121.0,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_valid_verified_citation_returns_first_try_no_retry(monkeypatch):
    parsed = {
        "category": "n_plus_one",
        "explanation": "N+1 query introduced inside the loop over orders.",
        "cited_lines": ["order.line_items = LineItem.objects.filter(order_id=order.id)"],
        "confidence": "high",
    }
    fake, calls = _fake_nemotron(parsed)
    monkeypatch.setattr(diagnoser, "call_nemotron", fake)
    refs = [TavilyRef(title="N+1 queries explained", url="https://example.com")]

    result = _diagnose(monkeypatch, tavily_refs=refs)

    assert result.category == "n_plus_one"
    assert result.confidence == "high"
    assert result.citation_verification_failed is False
    assert result.tavily_refs == refs
    assert len(calls) == 1  # no citation retry needed
    assert calls[0]["step"] == "diagnose"


def test_citation_with_diff_marker_stripped_still_verifies(monkeypatch):
    """AGENT_SPECS.md's own example output cites lines WITHOUT the diff's
    leading '+' — this must still verify, not trigger a spurious retry."""
    parsed = {
        "category": "n_plus_one",
        "explanation": "N+1 query in the loop.",
        "cited_lines": ["for order in orders:", "order.line_items = LineItem.objects.filter(order_id=order.id)"],
        "confidence": "high",
    }
    fake, calls = _fake_nemotron(parsed)
    monkeypatch.setattr(diagnoser, "call_nemotron", fake)

    result = _diagnose(monkeypatch)

    assert result.citation_verification_failed is False
    assert len(calls) == 1


def test_other_category_needs_no_citation_and_skips_tavily(monkeypatch):
    search_called = False

    def fake_search(*a, **k):
        nonlocal search_called
        search_called = True
        return []

    parsed = {
        "category": "other",
        "explanation": "Nothing in the diff clearly fits a known category.",
        "cited_lines": [],
        "confidence": "low",
    }
    fake, calls = _fake_nemotron(parsed)
    monkeypatch.setattr(diagnoser, "call_nemotron", fake)
    monkeypatch.setattr(diagnoser, "search_grounding", fake_search)

    result = diagnoser.diagnose(
        job_id="j1", guilty_commit_sha="abc123", diff=REAL_DIFF, surrounding_context="",
        commit_message="m", before_score=100.0, after_score=121.0,
    )

    assert result.category == "other"
    assert result.citation_verification_failed is False
    assert len(calls) == 1  # no retry triggered by empty citations on "other"
    assert not search_called  # grounding only runs for a real category


# ---------------------------------------------------------------------------
# Citation-verification retry / downgrade
# ---------------------------------------------------------------------------

def test_uncited_claim_triggers_exactly_one_retry_then_succeeds(monkeypatch):
    responses = iter([
        {
            "category": "algorithmic_complexity",
            "explanation": "Looks like nested loops now.",
            "cited_lines": ["this line does not appear in the diff anywhere"],
            "confidence": "medium",
        },
        {
            "category": "algorithmic_complexity",
            "explanation": "Corrected: nested loop over orders and line items.",
            "cited_lines": ["order.line_items = LineItem.objects.filter(order_id=order.id)"],
            "confidence": "medium",
        },
    ])
    calls = []

    def fake(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
        calls.append({"step": step, "payload": payload, "system_prompt": system_prompt})
        return NemotronResult(parsed=next(responses), raw_response="{}", attempts=1, model_id="fake", latency_s=0.0)

    monkeypatch.setattr(diagnoser, "call_nemotron", fake)

    result = _diagnose(monkeypatch)

    assert result.category == "algorithmic_complexity"
    assert result.citation_verification_failed is False  # retry succeeded, no downgrade
    assert len(calls) == 2
    assert calls[0]["step"] == "diagnose"
    assert calls[1]["step"] == "diagnose:citation_retry"
    assert "previous_response_with_unverified_citation" in calls[1]["payload"]
    assert diagnoser.CITATION_RETRY_ADDENDUM in calls[1]["system_prompt"]
    # base system prompt is carried forward verbatim on retry, not replaced
    assert diagnoser.DIAGNOSER_SYSTEM_PROMPT in calls[1]["system_prompt"]


def test_uncited_claim_still_uncited_after_retry_downgrades_to_other(monkeypatch):
    bad = {
        "category": "blocking_call",
        "explanation": "A blocking call was added.",
        "cited_lines": ["nothing_matching_the_diff()"],
        "confidence": "high",
    }

    def fake(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
        return NemotronResult(parsed=dict(bad), raw_response="{}", attempts=1, model_id="fake", latency_s=0.0)

    search_called_with = {}

    def fake_search(category, cited_lines, **k):
        search_called_with["category"] = category
        return []

    monkeypatch.setattr(diagnoser, "call_nemotron", fake)
    monkeypatch.setattr(diagnoser, "search_grounding", fake_search)

    result = diagnoser.diagnose(
        job_id="j1", guilty_commit_sha="abc123", diff=REAL_DIFF, surrounding_context="",
        commit_message="m", before_score=100.0, after_score=121.0,
    )

    assert result.category == "other"  # forced downgrade, never the model's original claim
    assert result.confidence == "low"
    assert result.citation_verification_failed is True
    assert "Auto-downgraded from 'blocking_call'" in result.explanation
    assert search_called_with == {}  # grounding must not run for the downgraded "other"


def test_empty_cited_lines_on_non_other_category_triggers_retry(monkeypatch):
    """An empty citation list is exactly the unearned-confidence case the
    check exists to catch — it must not silently pass."""
    responses = iter([
        {"category": "lost_cache", "explanation": "e", "cited_lines": [], "confidence": "high"},
        {"category": "other", "explanation": "Actually can't point to a specific line.", "cited_lines": [], "confidence": "low"},
    ])

    def fake(*, job_id, step, model, system_prompt, payload, temperature, **kwargs):
        return NemotronResult(parsed=next(responses), raw_response="{}", attempts=1, model_id="fake", latency_s=0.0)

    monkeypatch.setattr(diagnoser, "call_nemotron", fake)
    monkeypatch.setattr(diagnoser, "search_grounding", lambda *a, **k: [])

    result = _diagnose(monkeypatch)
    assert result.category == "other"
    assert result.citation_verification_failed is False  # model itself said "other" on retry, not forced


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_missing_field_raises_diagnoser_error(monkeypatch):
    parsed = {"category": "n_plus_one", "explanation": "e", "confidence": "high"}  # no cited_lines
    fake, _ = _fake_nemotron(parsed)
    monkeypatch.setattr(diagnoser, "call_nemotron", fake)

    with pytest.raises(DiagnoserError):
        _diagnose(monkeypatch)


def test_unknown_category_raises_diagnoser_error(monkeypatch):
    parsed = {"category": "not_a_real_category", "explanation": "e", "cited_lines": [], "confidence": "high"}
    fake, _ = _fake_nemotron(parsed)
    monkeypatch.setattr(diagnoser, "call_nemotron", fake)

    with pytest.raises(DiagnoserError):
        _diagnose(monkeypatch)


def test_unknown_confidence_raises_diagnoser_error(monkeypatch):
    parsed = {"category": "other", "explanation": "e", "cited_lines": [], "confidence": "super-duper-sure"}
    fake, _ = _fake_nemotron(parsed)
    monkeypatch.setattr(diagnoser, "call_nemotron", fake)

    with pytest.raises(DiagnoserError):
        _diagnose(monkeypatch)


# ---------------------------------------------------------------------------
# _verify_cited_lines / _normalize_diff_line — direct unit tests
# ---------------------------------------------------------------------------

def test_verify_cited_lines_rejects_hunk_header_context_not_actually_changed():
    """`def get_user_orders(user_id):` only appears in REAL_DIFF's hunk
    header (`@@ ... def get_user_orders(user_id): @@`) — unchanged context
    used to locate the hunk, never an added/removed line. A citation of it
    must NOT verify: citing unchanged code isn't citing the line
    responsible for the regression (AGENTS.md rule 5). This was the bug in
    CODE_REVIEW_FINDINGS.md #6 — the old raw-substring-of-the-whole-diff
    check let this pass."""
    assert not diagnoser._verify_cited_lines(["def get_user_orders(user_id):"], REAL_DIFF)


def test_verify_cited_lines_accepts_actual_added_line():
    assert diagnoser._verify_cited_lines(["order.line_items = LineItem.objects.filter(order_id=order.id)"], REAL_DIFF)


def test_verify_cited_lines_rejects_line_not_in_diff():
    assert not diagnoser._verify_cited_lines(["this_never_appears_anywhere()"], REAL_DIFF)


def test_verify_cited_lines_rejects_empty_list():
    assert not diagnoser._verify_cited_lines([], REAL_DIFF)


def test_normalize_diff_line_strips_leading_marker_but_not_file_header():
    assert diagnoser._normalize_diff_line("+    order.line_items = x") == "order.line_items = x"
    assert diagnoser._normalize_diff_line("--- a/orders.py") == "--- a/orders.py"
    assert diagnoser._normalize_diff_line("+++ b/orders.py") == "+++ b/orders.py"
