# Build Prompt — Demo Data, README & Submission Ops

**This isn't a code stack — it's a research and writing workstream**, and it's the one most likely to get squeezed if it doesn't have its own owner and its own file. Run it with a coding-agent session that has **web search enabled**, since the core task is finding real regressions, not writing code.

## Read first

- `AGENTS.md` "Things to avoid" (don't write demo cases from scratch/synthetically if avoidable)
- `PRD.md §10` (demo requirements), `§11` risk table row "Demo case looks synthetic/planted"
- `PROJECT_PLAN.md §1` Week 1 exit criteria, `§2` submission checklist, `§3` video script outline

## Non-negotiable

Real historical regressions from real OSS issue trackers are far more credible to judges — and to you, when validating that the Diagnoser's output actually matches ground truth — than anything synthesized. Don't fabricate a case just to hit a deadline.

## 1. Sourcing 2–3 demo cases

For each candidate case, verify all three before committing to it:

1. **Identifiable before/after commits** — a specific commit (or small PR) that a maintainer or issue thread already agrees caused a measurable slowdown, ideally with a follow-up commit/PR that fixed it.
2. **A runnable, fast, deterministic benchmark** exists or can be written that isolates the specific hot path in question — not a full end-to-end test suite (this becomes your `benchmark_command`).
3. **A clean root-cause category** — it should map to one of the Diagnoser's five categories (`n_plus_one`, `lost_cache`, `algorithmic_complexity`, `blocking_call`, `allocation_overhead`), or genuinely be a defensible `"other"`. If a case is too tangled to classify cleanly, don't force it — pick a different one.

Good hunting grounds for this kind of well-documented, discussed regression: actively-maintained Python libraries with a history of performance-focused issue threads (ORMs, web frameworks, data libraries, HTTP clients — the kind of project where "this got slower in v2.x" threads with a bisected culprit commit are common). Search issue trackers for phrasing like *"performance regression"*, *"got slower"*, or *"regression since"* combined with a resolving PR reference.

For each case you commit to, fill in `demo_cases/case_N_notes.md` with: repo URL, before/after commit SHAs, the benchmark command, the known ground-truth category, and a citation link (issue or PR URL) — this is what the Diagnoser/Fixer validation in `BUILD_02_BACKEND_AGENTS.md` checks against.

**I have web search available in this chat — if you'd like, I can run this sourcing search right now and come back with 3–5 shortlisted candidates for you to pick from, instead of you starting from a blank search.**

## 2. README

Must include (per `PRD.md §10` and the submission checklist below):

- OSS license visible at the top of the repo (MIT, Apache 2.0, or MPL 2.0 — pick one and add the `LICENSE` file at repo root)
- Setup instructions (this can mirror the "How to run locally" section already in `AGENTS.md` — keep both in sync rather than maintaining two versions)
- **Explicit callouts** for where Nemotron 3 Nano/Ultra, Token Factory Sandboxes, and Tavily accelerated the workflow — this is a stated judging requirement, not just nice-to-have context
- Link to the working demo URL and the ≤3-minute public YouTube video

## 3. Submission checklist (mirror of `PROJECT_PLAN.md §2` — keep this one updated as the source of truth)

| Requirement | Notes |
|---|---|
| Runs on Nebius Token Factory or AI Cloud | |
| Uses ≥1 NVIDIA open-source model | Nemotron 3 Nano + Ultra |
| Category selected | Coding & Agentic Engineering |
| Project description (what/why/how) | |
| Working demo URL | Required for this track |
| Demo video ≤3 min, public YouTube, narrated | Must cover how Token Factory + Nemotron were used |
| Public repo (GitHub/GitLab/Bitbucket) | |
| OSS license visible at top of repo | |
| README with setup instructions | |
| README highlights Nemotron/Token Factory/Nebius usage | |
| Feedback on Token Factory/AI Cloud/NVIDIA tools submitted | $100 x 10 "Most Valuable Feedback" prize |
| City selection for Builders & Brews (if applicable) | Shot at $500 City Winner Award |

## 4. Video script alignment

The script beats in `PROJECT_PLAN.md §3` assume: a visible cliff in the timeline chart (needs a real, sourced case — see §1), a live or pre-recorded sandbox bisection, Nemotron 3 Ultra's cited-lines explanation on screen, and a live-verified benchmark recovery. Whoever curates the final demo case for the video should pick the one with the cleanest, most visually obvious "before/after" story — not necessarily the technically hardest one.

## Hand back

Filled-in `demo_cases/case_1_notes.md` (through `case_3` if time allows), a draft `README.md`, the `LICENSE` file at repo root, and the submission checklist above with each row's real status.
