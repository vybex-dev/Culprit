# backend/report.py
"""
report.py — turns a finished job into a pull-request-ready write-up.

(PRD §9 P2: "Exportable PR-style patch + summary comment".) It's a pure
function of the job state, so it can only ever state what the job recorded:
  * "verified" appears only when fix.verified is true (AGENTS.md rule 1);
  * an unresolved fix is labelled as such, with an explicit don't-merge-blindly
    warning;
  * an offline run says so at the top.
"""

from __future__ import annotations

from typing import Any

from schema import JobState


def _fmt_ms(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.1f} ms" if v < 1000 else f"{v / 1000:,.2f} s"


def _pct(before: float | None, after: float | None) -> str:
    if not before or after is None:
        return "—"
    return f"{(after - before) / before * 100:+.1f}%"


CATEGORY_LABELS = {
    "n_plus_one": "N+1 queries",
    "lost_cache": "Lost cache",
    "algorithmic_complexity": "Algorithmic complexity",
    "blocking_call": "Blocking call on a hot path",
    "allocation_overhead": "Allocation / serialization overhead",
    "other": "Unclassified",
}


def render_report(job: JobState, metrics: dict[str, Any] | None = None) -> str:
    lines: list[str] = []
    add = lines.append
    sha = job.regression_commit
    commit = next((c for c in job.commits if c.sha == sha), None)
    regressed = next((t.score for t in job.timeline if t.commit == sha), None)
    baseline = job.baseline_score
    verified = bool(job.fix and job.fix.verified and job.final_result == "resolved")
    repo = job.repo_url.rstrip("/").removesuffix(".git").split("/")[-1] or job.repo_url

    if job.mode == "offline":
        add("> ⚠️ **Offline run.** Benchmarks below are real measurements; the diagnosis and fix were produced by "
            "Culprit's deterministic offline stand-in, *not* Nemotron.\n")

    if sha is None:
        add(f"# No performance regression found in `{repo}`\n")
        add(f"Every benchmarked commit in the range stayed within {job.threshold_pct or 15:g}% of the baseline "
            f"({_fmt_ms(baseline)}). Nothing to fix.\n")
    else:
        subject = f" — “{commit.subject}”" if commit and commit.subject else ""
        add(f"# perf: `{sha[:8]}` slowed `{repo}` by {_pct(baseline, regressed)}{subject}\n")
        add(f"**{_fmt_ms(baseline)} → {_fmt_ms(regressed)}** on `{job.benchmark_command}`"
            + (f", now **{_fmt_ms(job.fix.after_score)}** with the proposed patch (verified in a sandbox re-run)."
               if verified and job.fix else ".") + "\n")

    if sha is not None:
        add("| | Score | vs. baseline |")
        add("|---|---:|---:|")
        add(f"| Baseline (`{(job.commit_range or ['?'])[0][:8]}`) | {_fmt_ms(baseline)} | — |")
        add(f"| Regressed (`{sha[:8]}`) | {_fmt_ms(regressed)} | {_pct(baseline, regressed)} |")
        if job.fix:
            tag = "verified" if job.fix.verified else "NOT verified"
            add(f"| After proposed fix ({tag}) | {_fmt_ms(job.fix.after_score)} | {_pct(baseline, job.fix.after_score)} |")
        add("")
        if commit:
            add(f"**Guilty commit:** `{commit.sha[:12]}` by {commit.author or 'unknown'}"
                + (f" on {commit.date[:10]}" if commit.date else "") + f" — {commit.subject}\n")

    d = job.diagnosis
    if d:
        add("## Root cause\n")
        label = CATEGORY_LABELS.get(d.category, d.category)
        add(f"**{label}** · {d.confidence} confidence\n")
        add(d.explanation.strip() + "\n")
        if d.citation_verification_failed:
            add("_The model's citations could not be matched to the diff, so this was downgraded to “unclassified” "
                "rather than reported with false confidence._\n")
        if d.cited_lines:
            add("Lines cited (each verified as an actual changed line in the diff):\n")
            add("```")
            lines.extend(l.rstrip() for l in d.cited_lines)
            add("```\n")
        if d.tavily_refs:
            add("### Grounding (Tavily)\n")
            for r in d.tavily_refs:
                score = f" _(relevance {r.score:.2f})_" if r.score is not None else ""
                add(f"- [{r.title}]({r.url}){score}")
            add("")

    if job.fix:
        add("## Proposed fix\n")
        if verified:
            n = len(job.fix_attempts)
            add(f"✅ **Verified.** After applying this patch on top of `{sha[:8] if sha else '?'}`, the benchmark re-ran at "
                f"**{_fmt_ms(job.fix.after_score)}** (attempt {n}).\n")
        else:
            add("⚠️ **Not verified.** No patch brought the benchmark back within threshold. It is included for "
                "transparency — **do not merge it blindly.**\n")
        if job.fix_attempts:
            last = job.fix_attempts[-1]
            add(last.rationale.strip() + "\n")
        add("```diff")
        add(job.fix.patch_diff.rstrip())
        add("```\n")
        if len(job.fix_attempts) > 1:
            add(f"<sub>{len(job.fix_attempts)} attempts were made; scores after each: "
                + ", ".join(_fmt_ms(a.score_after) for a in job.fix_attempts) + ".</sub>\n")

    if job.probes:
        n_search = max(0, len(job.probes) - 1)
        add("## How it was found\n")
        add(f"Binary search over {len(job.commits)} commits needed **{n_search} benchmarked probes** "
            f"(a linear scan needs {max(len(job.commits) - 1, 1)}). Each probe ran {job.n_runs or '?'}× in a fresh, "
            "isolated sandbox; the median is compared to the baseline against a "
            f"{job.threshold_pct or 15:g}% threshold.\n")
        add("| # | Commit | Subject | Median | Δ baseline | Verdict |")
        add("|--:|---|---|--:|--:|---|")
        subj = {c.sha: c.subject for c in job.commits}
        for p in sorted(job.probes, key=lambda p: p.step):
            add(f"| {p.step} | `{p.commit[:8]}` | {subj.get(p.commit, '')[:48]} | {_fmt_ms(p.median_score)} | "
                f"{p.pct_change:+.1f}% | {p.verdict} |")
        add("")

    if metrics:
        add("## Models & infrastructure used\n")
        for role, m in (metrics.get("models") or {}).items():
            name = {"nano": "Nemotron 3 Nano (control flow)", "ultra": "Nemotron 3 Ultra (reasoning)"}.get(role, role)
            add(f"- **{name}** — `{m.get('model_id', '')}` · {m.get('calls', 0)} call(s) · "
                f"{m.get('total_tokens', 0):,} tokens · avg {m.get('avg_latency_s', 0):.2f}s")
        sb = metrics.get("sandbox") or {}
        add(f"- **Sandboxes** — {sb.get('runs', 0)} benchmark runs across {sb.get('probes', 0)} probes "
            f"({sb.get('patched_runs', 0)} on patched code)")
        tv = metrics.get("tavily") or {}
        if tv.get("searches"):
            add(f"- **Tavily** — {tv['searches']} search(es), {tv.get('sources', 0)} source(s) kept")
        add("")

    add("---")
    add(f"<sub>Generated by Culprit · job `{job.job_id}` · benchmark "
        f"`{job.benchmark_command}` · mode: {job.mode}</sub>")
    return "\n".join(lines) + "\n"
