# Build Prompt — Backend: Core Orchestration & Sandbox Infra

**You are building the control-flow spine of Performance Regression Detective**: the API layer, the Nemotron model wrapper, the Token Factory Sandbox client, and the Bisector's binary-search loop. Nothing in this stack does open-ended reasoning — every decision here is either deterministic code or a narrow, low-temperature Nano call. Deep reasoning (Diagnoser, Fixer) belongs to the other backend stack, not this one.

## Read first

- `AGENTS.md` (whole file — this is the repo's constitution)
- `BUILD_00_OVERVIEW.md` — canonical job-state schema and the two flagged open decisions (benchmark methodology, regression threshold). Confirm both are resolved before you start `bisector.py`.
- `AGENT_SPECS.md §0` (shared conventions) and `§1` (Bisector agent)
- `TRD.md §2.1` (Bisector), `§3` (data flow/interfaces), `§4` (sandbox execution design)

## Install first

```bash
npx skills add akmalovaa/python-skill
# opinionated stack: uv, ruff, pyright, pydantic v2, httpx, structlog, pytest;
# fail-loud error handling — no swallowed exceptions, no fake fallback data.
# This matters here specifically: rule 1 below ("never fabricate") is a
# code-review problem, not just a prompt-writing one.

/plugin marketplace add obra/superpowers-marketplace
/plugin install superpowers@superpowers-marketplace
# gives you systematic-debugging and TDD workflows — use them for the
# bisection binary-search logic, which is exactly the kind of "subtle
# off-by-one under noise" bug that benefits from a structured approach
# rather than trial-and-error.

T=$(mktemp -d) && git clone --depth=1 https://github.com/majiayu000/claude-skill-registry "$T" \
  && mkdir -p ~/.claude/skills && cp -r "$T/skills/data/backend-fastapi" ~/.claude/skills/backend-fastapi \
  && rm -rf "$T"
# FastAPI patterns (dependency injection, async, clean layering) for api.py.
```

## What you own

```
backend/api.py             # job endpoints (POST /analyze, GET /jobs/{job_id})
backend/models.py          # Nemotron API wrapper (Nano/Ultra calls, shared by every agent)
backend/sandbox_client.py  # Token Factory Sandbox wrapper
backend/bisector.py        # binary search orchestration, calls Nano
```

## Non-negotiables specific to this stack

1. **Never fabricate benchmark results.** If a sandbox run fails, the job fails with a clear error — it does not silently retry into a fake number.
2. **Every candidate commit gets a fresh, isolated sandbox.** No shared state between runs. This is what makes the benchmark numbers comparable and credible — don't optimize it away for speed.
3. **Agent outputs must match the exact JSON schemas below.** If you need to change one, update `AGENT_SPECS.md` first, not silently in code.
4. **Nano only, for control-flow only.** Don't route Bisector decisions to Ultra "just to be safe" — the tiered-model story is part of the submission narrative.

## 1. The job-state contract (`api.py`)

Use the reconciled schema from `BUILD_00_OVERVIEW.md`, not the two conflicting versions in the source docs. Endpoints:

```
POST /analyze
  { repo_url, benchmark_command, commit_range? }
  → { job_id }

GET /jobs/{job_id}
  → full job-state object (see BUILD_00_OVERVIEW.md canonical schema)
```

Keep this a simple job-based API even if it's backed by a single in-process store for the hackathon (per `TRD.md §3`) — the point is that the frontend can poll and render progressively, not that you need real persistence yet. Structure it so swapping in a real datastore later is a one-file change, not a rewrite: put all state reads/writes behind a small `JobStore` interface from day one.

Status lifecycle: `queued → bisecting → diagnosing → fixing → done | failed`. `regression_commit` going non-null while still `bisecting`/`diagnosing` is what the frontend uses to render the "found regression" moment — you don't need a separate status value for it.

## 2. Model wrapper (`models.py`)

One function, one contract, used by every agent (including the ones in the other backend stack):

```python
def call_nemotron(model: Literal["nano", "ultra"], system_prompt: str, payload: dict, temperature: float) -> dict:
    ...
```

Malformed-output handling (verbatim from `AGENT_SPECS.md §0`):

> If a response fails JSON parsing, retry once with the original prompt plus: "Your previous response was not valid JSON. Return ONLY valid JSON matching the schema, with no other text." If it fails twice, log the raw output and fail the job with a clear error rather than guessing.

Logging (verbatim): **log every prompt + raw response pair, keyed by `job_id` and step.** This is both a debugging tool and part of your "inspectable, not a black box" story for judges — don't treat it as optional instrumentation you'll add later.

Temperature: 0.1–0.3 for Bisector and Fixer (consistency over creativity); 0.3–0.5 acceptable for Diagnoser's *explanation* phrasing only — never for the classification field itself.

## 3. Sandbox client (`sandbox_client.py`)

Per candidate commit:
1. Checkout the commit in a fresh Token Factory Sandbox.
2. Build/install dependencies — **cache this step** by lockfile hash so a 5-week build with limited free credits doesn't burn them on redundant installs.
3. Run the benchmark **N times (3–5)**; log every raw score, not just the median (needed for noise inspection/debugging per `TRD.md §4`).
4. Return the raw scores up the stack — median/threshold comparison is the Bisector's job, not this client's.

Benchmark script must be deterministic and quick (seconds, not minutes) and isolate the specific hot path being demoed — not a full end-to-end suite.

## 4. Bisector (`bisector.py`)

Binary search over the commit range. For each candidate, after the sandbox returns scores, call Nano with this **exact** schema:

Input:
```json
{
  "commit_range": ["<start_sha>", "<end_sha>"],
  "candidate_sha": "<sha_being_evaluated>",
  "candidate_scores": [123.4, 119.8, 121.0],
  "baseline_score": 100.0,
  "regression_threshold_pct": 15
}
```

Output:
```json
{
  "verdict": "regressed" | "clean" | "inconclusive",
  "median_score": 121.0,
  "pct_change_from_baseline": 21.0,
  "additional_runs_needed": 0
}
```

If `verdict == "inconclusive"`, collect `additional_runs_needed` more sandbox runs for that candidate before re-asking Nano — don't just re-run once and hope. Narrow the search range using `verdict` until you converge on the earliest `"regressed"` commit. Output: the guilty commit hash, plus the full `{commit, score, timestamp}` timeline for the dashboard.

## Testing & validation (do this before wiring in real sandboxes)

- Unit test the bisection logic against a **synthetic mock commit history** with a known regression point — fast, no sandbox cost.
- Stress-test noise handling: deliberately inject noise into a stable commit's repeated runs and confirm the system reports `"clean"` or `"inconclusive"`, never a false-positive `"regressed"`.
- Non-functional target: correctly identifies the guilty commit despite ±10% benchmark noise.

## Definition of done

Given a mock/synthetic commit history with a known regression point, the Bisector correctly narrows to it within a fixed number of steps, and correctly reports `"inconclusive"` when noise is deliberately injected above threshold.

## Suggested build order

1. `sandbox_client.py` against a trivial real repo (no model calls yet) — get "checkout → run → number back" rock solid first, per `PROJECT_PLAN.md` Week 1 exit criteria.
2. `models.py` wrapper + retry/logging, tested against a stub schema unrelated to this project.
3. `bisector.py` against the mock commit history (TDD: write the noise/convergence tests first).
4. `api.py` wiring the above behind `POST /analyze` / `GET /jobs/{job_id}`.

## Hand back

A short write-up of: the reconciled schema you actually implemented (confirm it matches `BUILD_00_OVERVIEW.md` or note the diff), test results for the mock-history and noise-injection cases, and one real end-to-end run's job JSON (repo in → guilty commit + timeline out) with no model errors swallowed.
