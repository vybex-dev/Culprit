# backend/tests/test_report.py
"""The PR write-up may only claim what the job recorded (AGENTS.md rule 1)."""

from report import render_report
from schema import CommitModel, DiagnosisModel, FixAttemptModel, FixModel, JobState, ProbeModel, TavilyRefModel, TimelineEntryModel

SHA = "e1481e7286dca276746d8a04b40bc6ec3ce1acbf"
START = "4798bbe6bd86d6f519efecd8237acf735b9dac9c"


def _job(**kw):
    base = dict(
        job_id="abc", status="done", repo_url="https://github.com/o/orders-service.git",
        benchmark_command="python bench.py", commit_range=[START, "f" * 40],
        created_at="t", updated_at="t", baseline_score=9.7, threshold_pct=15, n_runs=5,
        regression_commit=SHA,
        timeline=[TimelineEntryModel(commit=START, score=9.7, timestamp="t"),
                  TimelineEntryModel(commit=SHA, score=297.9, timestamp="t")],
        commits=[CommitModel(index=0, sha=START, subject="bench"),
                 CommitModel(index=1, sha=SHA, subject="refactor: simplify loading", author="Mei", date="2026-09-12T00:00:00Z")],
        probes=[ProbeModel(step=0, commit=START, index=0, role="baseline", raw_scores=[9.7], median_score=9.7,
                           pct_change=0, verdict="baseline"),
                ProbeModel(step=1, commit=SHA, index=1, role="bisect", raw_scores=[297.9], median_score=297.9,
                           pct_change=2970, verdict="regressed")],
        diagnosis=DiagnosisModel(category="n_plus_one", explanation="One query per order.",
                                 cited_lines=["order['x'] = db.query(1)"], confidence="high",
                                 tavily_refs=[TavilyRefModel(title="N+1 explained", url="https://e.com/n1", score=0.91)]),
    )
    base.update(kw)
    return JobState(**base)


def test_verified_fix_is_reported_as_verified():
    job = _job(
        fix=FixModel(patch_diff="--- a/x\n+++ b/x\n@@\n-a\n+b\n", verified=True, before_score=9.7, after_score=9.8),
        fix_attempts=[FixAttemptModel(attempt=1, patch="p", rationale="Batch the lookup.", score_after=9.8, resolved=True)],
        final_result="resolved",
    )
    md = render_report(job, {"models": {"nano": {"model_id": "n", "calls": 6, "total_tokens": 10, "avg_latency_s": 0.2}},
                             "sandbox": {"runs": 35, "probes": 7, "patched_runs": 5}, "tavily": {"searches": 1, "sources": 1}})
    assert "perf: `e1481e72`" in md and "“refactor: simplify loading”" in md
    assert "✅ **Verified.**" in md and "```diff" in md
    assert "N+1 explained" in md and "relevance 0.91" in md
    assert "7 benchmarked probes" not in md.split("How it was found")[0]  # sanity: section ordering
    assert "1 benchmarked probes" in md  # len(probes)-1
    assert "Nemotron 3 Nano" in md


def test_unverified_fix_is_never_called_verified():
    job = _job(fix=FixModel(patch_diff="--- a/x\n+++ b/x\n", verified=False, before_score=9.7, after_score=250),
               fix_attempts=[FixAttemptModel(attempt=1, patch="p", rationale="tried", score_after=250, resolved=False)],
               final_result="unresolved_diagnosis_only")
    md = render_report(job)
    assert "✅" not in md and "Verified" not in md.replace("NOT verified", "")
    assert "do not merge it blindly" in md


def test_no_regression_and_offline_banner():
    job = _job(regression_commit=None, diagnosis=None, mode="offline", probes=[], timeline=[])
    md = render_report(job)
    assert md.startswith("> ⚠️ **Offline run.**") and "No performance regression found" in md
