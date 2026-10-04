# AGENTS.md

## Guide for AI coding agents working on this repository

This file is for any AI coding assistant (Claude Code or similar) helping build this project. Read this before making changes. Full context lives in `docs/PRD.md`, `docs/TRD.md`, and `docs/AGENT_SPECS.md` — read those too if the task touches architecture or the internal agent prompts.

## Project in one paragraph

An agent that takes a repo + a benchmark, bisects commit history in Token Factory Sandboxes to find exactly where performance regressed, diagnoses why using Nemotron 3 Ultra (with Tavily grounding), proposes and verifies a fix, and presents it all on a timeline dashboard. Built for the Nebius x NVIDIA Global AI Hackathon, Coding & Agentic Engineering track, due Oct 30, 2026.

## Repo structure

```
/perf-regression-detective
├── README.md
├── LICENSE
├── backend/
│   ├── bisector.py       # binary search orchestration, calls Nano
│   ├── diagnoser.py       # root-cause classification, calls Ultra + Tavily
│   ├── fixer.py           # patch generation + retry loop, calls Ultra
│   ├── sandbox_client.py  # Token Factory Sandbox wrapper
│   ├── models.py          # Nemotron API wrapper (Nano/Ultra calls)
│   ├── tavily_client.py
│   └── api.py             # job endpoints (POST /analyze, GET /jobs/{id})
├── frontend/               # dashboard app
├── demo_cases/             # curated real regressions + source links
└── docs/
    ├── PRD.md
    ├── TRD.md
    ├── AGENT_SPECS.md
    └── PROJECT_PLAN.md
```

## Non-negotiable rules

1. **Never fabricate benchmark results.** If a sandbox run fails or a fix doesn't verify, report that honestly in the job state. A correct "diagnosis only, fix unresolved" result is a legitimate outcome — a fake "resolved: true" is not.
2. **Every candidate commit gets a fresh, isolated sandbox.** No shared state between benchmark runs — this is required for the numbers to be comparable and credible.
3. **Agent outputs must match the exact JSON schemas in `docs/AGENT_SPECS.md`.** Don't loosen or improvise the schema without updating that doc first.
4. **Model routing matters for the hackathon story**: Nano for control-flow/triage (Bisector), Ultra for reasoning (Diagnoser, Fixer). Don't default everything to one model — the tiered usage is part of the submission's technical narrative.
5. **The Diagnoser must cite specific diff lines.** A diagnosis with no citation should be classified as `"other"` with an honest explanation, not dressed up as a confident specific category.

## How to run locally

```
# backend  (no keys? prefix with CULPRIT_OFFLINE=1 — real benchmarks, labelled stand-in models)
cd backend
pip install -r requirements.txt
cp .env.example .env      # NEBIUS_API_KEY, CONTREE_PROJECT, CONTREE_IMAGE, TAVILY_API_KEY
python api.py

# frontend
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Tests: `make test` (backend pytest + frontend unit tests/typecheck/lint). Before a real run, open `/new` — its readiness panel (`GET /preflight`) verifies keys and model IDs against the live catalog.

(Fill in real setup steps here as they're built — keep this section accurate at all times, since it doubles as the README's setup section.)

## Definition of done, per component

- **Bisector**: given a mock/synthetic commit history with a known regression point, correctly narrows to it within a fixed number of steps, and correctly reports "inconclusive" when noise is deliberately injected above threshold.
- **Diagnoser**: on all curated demo cases, output category matches the known ground-truth cause, and `cited_lines` actually appears in the provided diff.
- **Fixer**: on at least 2 of 3 curated demo cases, produces a patch that brings the benchmark back within ~10% of baseline, verified by an actual sandbox re-run (not assumed).
- **Dashboard**: a person with no prior context can look at it for 60 seconds and correctly explain what regressed, why, and whether it was fixed.

## What "good" looks like for this hackathon specifically

Judging is on Technical Implementation, Design, Potential Impact, and Quality of Idea — equally weighted. When in doubt about where to spend limited time, don't over-invest in one dimension (e.g., a beautiful dashboard around a fake/mocked backend, or a flawless backend with a bare terminal output) — all four need to be credible.

## Things to avoid

- Don't add support for multiple languages/frameworks — depth on one ecosystem (recommended: Python) beats shallow breadth.
- Don't build CI/production-monitoring integration — out of scope per PRD, time is better spent elsewhere.
- Don't skip caching dependency installs in the sandbox — repeated full installs across many commits will burn credits and time fast.
- Don't write demo cases from scratch/synthetically if avoidable — real historical regressions from real OSS issue trackers are far more credible to judges and to yourselves when validating correctness.

## When context is missing

If a task requires a decision not covered here or in the PRD/TRD (e.g., exact benchmark metric, exact regression threshold), don't guess silently — flag it explicitly rather than picking an arbitrary default that could quietly diverge from what the rest of the team assumes.
