# Culprit

**Finds the exact commit where your benchmark got slower, explains why, and proves the fix.**

Built for the Nebius x NVIDIA Global AI Hackathon (Coding & Agentic Engineering track). Culprit bisects a repo's commit history in Token Factory Sandboxes to locate a performance regression, diagnoses the root cause with Nemotron 3 Ultra (grounded with Tavily), proposes and sandbox-verifies a fix, and lays the whole thing out on a timeline dashboard — real numbers, not vibes.

> Working name — see `docs/PROJECT_PLAN.md` and the naming note in this README's history for alternatives if you'd rather change it before the repo goes public.

## Status

Scaffold only. Nothing below `backend/`, `frontend/`, or `demo_cases/` is implemented yet — each file currently holds a placeholder pointing at the spec that governs it.

## Repo layout

```
culprit/
├── README.md              # this file
├── LICENSE                 # MIT
├── AGENTS.md               # read this first if you're an AI coding agent working on this repo
├── backend/
│   ├── api.py              # job endpoints (POST /analyze, GET /jobs/{job_id})
│   ├── models.py           # Nemotron API wrapper (Nano/Ultra), retry + logging
│   ├── sandbox_client.py   # Token Factory Sandbox wrapper
│   ├── bisector.py         # binary search orchestration (Nano)
│   ├── diagnoser.py        # root-cause classification (Ultra + Tavily)
│   ├── fixer.py            # patch generation + retry loop (Ultra)
│   ├── tavily_client.py
│   ├── requirements.txt
│   └── tests/
├── frontend/                # Next.js dashboard (not yet scaffolded)
├── demo_cases/               # 2-3 curated real historical regressions (ground truth)
└── docs/
    ├── PRD.md
    ├── TRD.md
    ├── AGENT_SPECS.md
    ├── PROJECT_PLAN.md
    └── build-prompts/        # per-stack build prompts for a coding agent
        ├── BUILD_00_OVERVIEW.md
        ├── BUILD_01_BACKEND_CORE.md
        ├── BUILD_02_BACKEND_AGENTS.md
        ├── BUILD_03_FRONTEND_DASHBOARD.md
        └── BUILD_04_DEMO_AND_SUBMISSION.md
```

## How this repo gets built

Each file in `docs/build-prompts/` is a self-contained brief for one stack. Open a fresh agent session, point it at `AGENTS.md` + the relevant build-prompt file, and let it work through that file top to bottom. `BUILD_00_OVERVIEW.md` has the full map, the shared job-state schema, and two spec decisions (benchmark methodology, regression threshold) that need to be locked in before backend work starts.

## Setup

*(Fill in as `backend/` and `frontend/` get built — keep this in sync with `AGENTS.md`'s "How to run locally" section.)*

```bash
# backend
cd backend
pip install -r requirements.txt
export NEBIUS_API_KEY=...
export TAVILY_API_KEY=...
python api.py

# frontend
cd frontend
npm install
npm run dev
```

## Nemotron / Token Factory / Tavily usage

*(Required by the hackathon rules — fill in once the pipeline runs end-to-end.)* Nemotron 3 Nano handles the control-flow decisions in the Bisector; Nemotron 3 Ultra handles the reasoning in the Diagnoser and Fixer; Token Factory Sandboxes give every candidate commit a fresh, isolated benchmark run; Tavily grounds the Diagnoser's output against known issues/anti-patterns.

## License

MIT — see `LICENSE`.
