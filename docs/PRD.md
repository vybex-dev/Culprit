# Product Requirements Document

## Performance Regression Detective

*Nebius x NVIDIA Global AI Hackathon — Coding & Agentic Engineering Track*

---

## 1. Problem Statement

Software performance regressions are common, expensive, and slow to diagnose. A single commit can quietly double a hot path's latency, and the standard debugging path — manual `git bisect` plus guesswork — can eat hours or days of an engineer's time. Most teams either catch regressions by luck (a user complains) or don't catch them at all.

There is no widely-used, end-to-end tool that automatically **finds**, **explains**, and **verifiably fixes** a performance regression in a codebase.

## 2. Product Vision

An agent that takes a repository and a benchmark, automatically finds the exact commit where performance dropped, explains *why* in plain English, proposes a fix, and proves the fix works — all with real numbers, not vibes.

## 3. Goals

| Goal | Success Metric |
| --- | --- |
| Automatically locate the regressing commit | Correctly bisects to the guilty commit on 100% of curated demo cases |
| Explain root cause credibly | Diagnosis matches the actual known cause on curated cases; on unseen cases, diagnosis is plausible and specific (not generic) |
| Produce a verified fix | Post-fix benchmark recovers to within ~10% of pre-regression baseline on at least 2 of 3 demo cases |
| Deliver a coherent product experience | A judge can understand what happened and why within 60 seconds of viewing the dashboard, with no narration needed |
| Win Tavily bonus | Tavily is used in a way that measurably improves diagnosis quality (root-cause verification against known issues/changelogs) |

## 4. Non-Goals (Out of Scope for the Hackathon)

- Supporting arbitrary languages/frameworks — pick 1 (e.g., Python or Node) and go deep rather than wide.
- Continuous/production monitoring integration (CI webhook, Slack bot, etc.) — nice-to-have stretch, not core.
- Fixing every category of regression — a strong, credible subset (N+1 queries, lost caching, accidental algorithmic complexity, added blocking calls) is enough.
- Multi-repo or monorepo support.

## 5. Target User

A backend/platform engineer or team lead who owns performance for a service and currently relies on manual bisection or user complaints to catch regressions. Secondary audience: OSS maintainers who care about benchmark stability across contributions.

## 6. Judging Criteria Alignment

| Criterion | How this product addresses it |
| --- | --- |
| **Technological Implementation** | Real use of Token Factory Sandboxes for repeated isolated benchmark runs across commit history; tiered model use (Nano for control-flow/triage, Ultra for diagnosis and patch generation) |
| **Design** | A timeline dashboard, not a terminal log — drill into any cliff to see diagnosis, diff, and before/after numbers |
| **Potential Impact** | A specific, real, expensive problem every engineering org has; no dominant free tool solves it end-to-end today |
| **Quality of Idea** | Bisection + root-cause reasoning + verified-fix loop is a distinct pipeline, not a repackaged existing category (unlike dependency-upgraders or generic code-review bots) |

## 7. Core Features (P0 — must have for submission)

1. **Bisector**: given a repo + benchmark command, binary-searches commit history using sandboxed runs to find the exact commit where a benchmark score crosses a regression threshold.
2. **Diagnoser**: given the guilty commit's diff (plus surrounding file context), classifies and explains the root cause in plain English (e.g., "added N+1 query in `get_user_orders`").
3. **Fixer + Verifier**: proposes a patch, applies it in a sandbox, re-runs the benchmark, and reports whether the regression is resolved. Loops up to a fixed attempt limit.
4. **Dashboard**: a timeline chart of benchmark score per commit, with a clickable regression point that expands into diagnosis, diff, and before/after numbers.

## 8. Features (P1 — strong to have)

5. **Tavily-grounded diagnosis**: cross-check the diagnosis against known issues, changelogs, or common anti-pattern write-ups to add citations/confidence to the explanation.
6. **Multi-case support**: run against 2–3 curated real-world repos with known historical regressions, so the demo shows generalization, not one cherry-picked case.

## 9. Features (P2 — stretch, only if time remains)

7. Exportable PR-style patch + summary comment, formatted as if opening a real pull request.
8. Simple CI-trigger simulation ("on every PR, run this against the last 20 commits").

## 10. Demo Requirements (per hackathon rules)

- Working demo URL (hosted dashboard or clear local test build instructions)
- ≤3-minute public YouTube video: show a real regression case, the bisection running, the diagnosis, the fix, and the recovered benchmark number, with narration on how Nemotron and Token Factory were used
- Public repo with OSS license visible at the top + README with setup instructions
- README must explicitly call out where Nemotron models and Token Factory accelerated the workflow, plus any other Nebius/Tavily tools used

## 11. Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| Benchmarks are flaky/noisy, causing false bisection results | Run each candidate commit multiple times, use median/trimmed-mean, define a clear statistical threshold for "regressed" |
| Diagnosis is generic/hand-wavy on unseen cases | Constrain the diagnoser prompt to specific, checkable categories; require it to cite the exact lines/diff hunk responsible |
| Fix loop never converges | Hard cap attempts (e.g., 3); if unresolved, still report the root cause — that alone is valuable and honest |
| Demo case looks synthetic/planted | Source 2–3 real historical regressions from real OSS repos (search issue trackers for "performance regression" + a resolving PR) rather than fabricating one |

## 12. Success Definition for the Hackathon

A submission is successful if a judge who has never seen the project can watch the 3-minute video, understand the problem and the payoff without confusion, and come away believing this could be a real product — not just a hackathon toy.
