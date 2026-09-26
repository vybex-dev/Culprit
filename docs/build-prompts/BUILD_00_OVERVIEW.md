# Build Overview — Performance Regression Detective

This file is a companion to your existing `PRD.md`, `TRD.md`, `AGENTS.md`, `AGENT_SPECS.md`, and `PROJECT_PLAN.md`. Those documents describe *what* to build. The four files below (`BUILD_01`–`BUILD_04`) turn that into *ready-to-run task briefs* — one per tech stack — so you can hand each one to a coding agent (Claude Code or similar) in its own session and get consistent, non-conflicting output.

Drop all five `BUILD_*.md` files into `docs/build-prompts/` alongside your existing docs.

## How to use these files

1. Open a **fresh agent session per stack**. Don't run all four in one giant context — the whole point is that each session only needs to hold one stack's worth of decisions in its head.
2. At the start of each session, tell the agent to read, in order: `AGENTS.md` → the relevant `BUILD_0X_*.md` → whichever of `PRD.md` / `TRD.md` / `AGENT_SPECS.md` sections that build file points to.
3. Install the skill(s) listed in that stack's file *before* it starts writing code.
4. Work top to bottom inside the file — each one is ordered so earlier sections unblock later ones.

## Stack map

| # | Stack | File | Owns (repo paths) | Model/tech | Matches team role (Project Plan §4) |
|---|-------|------|---------------------|------------|--------------------------------------|
| 1 | Backend — Core Orchestration & Sandbox Infra | `BUILD_01_BACKEND_CORE.md` | `backend/api.py`, `backend/models.py`, `backend/sandbox_client.py`, `backend/bisector.py` | Nemotron 3 Nano, Token Factory Sandboxes | Sandbox/infra + half of Agent/model logic |
| 2 | Backend — AI Reasoning Agents | `BUILD_02_BACKEND_AGENTS.md` | `backend/diagnoser.py`, `backend/fixer.py`, `backend/tavily_client.py` | Nemotron 3 Ultra, Tavily | Other half of Agent/model logic |
| 3 | Frontend — Timeline Dashboard | `BUILD_03_FRONTEND_DASHBOARD.md` | `frontend/` | Next.js, Recharts/D3 | Frontend/dashboard |
| 4 | Demo Data, README & Submission Ops | `BUILD_04_DEMO_AND_SUBMISSION.md` | `demo_cases/`, `README.md`, `LICENSE` | Research/writing, not code | Demo/storytelling |

Stacks 1 and 2 can run sequentially in the same repo. Stack 3 can start **in parallel** with stack 2 once the job-state contract below is frozen — the frontend only needs a fixture that matches the schema, it doesn't need the real backend running yet. Stack 4 runs alongside everything else starting Week 1.

## ⚠️ Spec conflict to resolve before Stack 1 starts

Your own docs disagree on the job-state / API response shape. `AGENT_SPECS.md §4` defines:

```json
{
  "job_id": "abc123",
  "status": "diagnosing" | "bisecting" | "fixing" | "done" | "failed",
  "timeline": [{"commit": "sha", "score": 100.0}],
  "regression_commit": "sha",
  "diagnosis": { "...": "Diagnoser output" },
  "tavily_refs": [{"title": "...", "url": "..."}],
  "fix_attempts": [{"attempt": 1, "patch": "...", "score_after": 118.0, "resolved": false}],
  "final_result": "resolved" | "unresolved_diagnosis_only"
}
```

`TRD.md §3` defines `GET /jobs/{job_id}` as returning:

```
{ status, timeline: [{commit, score, timestamp}], regression_commit,
  diagnosis: { category, explanation, cited_lines, tavily_refs },
  fix: { patch_diff, verified: bool, before_score, after_score } }
```

Differences: `timeline` entries have a `timestamp` in one and not the other; `fix_attempts` (array, full retry history) vs. `fix` (single object) don't match; `final_result` exists in one but not the other. Per your own `AGENTS.md` rule 3 ("agent outputs must match the exact schemas... don't loosen or improvise without updating that doc first"), whoever writes `api.py` needs one canonical answer, not two docs to reconcile mid-build.

**Recommended reconciliation** (keeps everything either doc needs — treat as the canonical shape for Stack 1 unless you overrule it):

```json
{
  "job_id": "abc123",
  "status": "queued" | "bisecting" | "diagnosing" | "fixing" | "done" | "failed",
  "repo_url": "...",
  "benchmark_command": "...",
  "timeline": [{"commit": "sha", "score": 100.0, "timestamp": "..."}],
  "regression_commit": "sha",
  "diagnosis": {
    "category": "...", "explanation": "...", "cited_lines": ["..."],
    "confidence": "high", "tavily_refs": [{"title": "...", "url": "..."}]
  },
  "fix_attempts": [{"attempt": 1, "patch": "...", "rationale": "...", "score_after": 118.0, "resolved": false}],
  "fix": {
    "patch_diff": "<patch from the last attempt>",
    "verified": false,
    "before_score": 100.0,
    "after_score": 118.0
  },
  "final_result": "resolved" | "unresolved_diagnosis_only"
}
```

`fix` is a convenience view derived from the last entry of `fix_attempts` — the frontend can bind to it directly without knowing about retries, while `fix_attempts` gives the full honest history for anyone who clicks in. `queued` is added as the state between `POST /analyze` returning a `job_id` and the first sandbox run actually starting — the dashboard's "found regression" moment is derived by the frontend, not a separate status: it's `regression_commit != null` while `status` is still `bisecting`/`diagnosing`.

Flag this to whoever owns the spec docs and update `AGENT_SPECS.md §4` and `TRD.md §3` to match before Stack 1 is marked done — this file is not a substitute for that, it's a stopgap so building isn't blocked.

## Two open decisions from TRD §9 that are still genuinely open

`TRD.md §9` lists three open questions. Ecosystem is already decided (Python, per `AGENTS.md`). The other two aren't optional trivia — they change what `bisector.py` and the sandbox benchmark script look like, so pin them **before** Stack 1:

1. **Benchmark methodology** — recommend **wall-clock time** (seconds/ms via `timeit` or `pytest-benchmark`) over throughput or a custom score. It's the simplest thing that's comparable across arbitrary demo repos and lines up with the "score" language already used everywhere in your schemas.
2. **Regression threshold** — the example payload in `AGENT_SPECS.md` uses `"regression_threshold_pct": 15`. Recommend keeping **15%** as the actual default: your NFR target is correctness "despite ±10% benchmark noise," so 15% gives a real margin above the noise floor without being so loose it misses real regressions.

Both are defaults you can accept as-is — just don't let Stack 1 start with this silently unresolved in someone's head.

## Environment variables (shared across stacks)

```
NEBIUS_API_KEY=...          # Token Factory model access (Nano + Ultra)
TAVILY_API_KEY=...          # Diagnoser grounding step
SANDBOX_CACHE_DIR=...       # where cached dependency-install layers live, keyed by lockfile hash
NEXT_PUBLIC_API_BASE_URL=...# frontend → backend, e.g. http://localhost:8000
```

## Non-negotiables that apply to every stack (verbatim from `AGENTS.md`, don't relitigate per-session)

1. Never fabricate benchmark results. A "diagnosed but not auto-fixed" result is legitimate; a fake `resolved: true` is not.
2. Every candidate commit gets a fresh, isolated sandbox. No shared state between runs.
3. Agent outputs must match the exact JSON schemas in `AGENT_SPECS.md` (as reconciled above). Don't improvise the schema mid-build.
4. Model routing matters for the submission story: Nano for control-flow (Bisector), Ultra for reasoning (Diagnoser, Fixer). Don't collapse this to one model for convenience.
5. The Diagnoser must cite specific diff lines, or it must honestly classify as `"other"`.

## Skill installs, all in one place

You'll see these again with more context in each stack's file — here's the full list if you want to install everything up front:

```bash
# Backend (stacks 1 & 2)
npx skills add akmalovaa/python-skill          # opinionated Python stack + fail-loud conventions
/plugin marketplace add obra/superpowers-marketplace
/plugin install superpowers@superpowers-marketplace   # TDD / systematic-debugging / planning workflows
T=$(mktemp -d) && git clone --depth=1 https://github.com/majiayu000/claude-skill-registry "$T" \
  && mkdir -p ~/.claude/skills && cp -r "$T/skills/data/backend-fastapi" ~/.claude/skills/backend-fastapi \
  && rm -rf "$T"                                  # FastAPI architecture patterns

# Frontend (stack 3)
npx skills@latest add emilkowalski/skills        # emil-design-eng, review-animations, and related
```

That's the map. The four stack files follow.
