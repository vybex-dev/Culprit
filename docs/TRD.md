# Technical Requirements Document

## Performance Regression Detective

---

## 1. System Architecture

```
flowchart TD
    A[User submits: repo URL + benchmark command] --> B[Bisector]
    B -->|spins up per-commit runs| C[Token Factory Sandbox]
    C -->|benchmark score| B
    B -->|guilty commit found| D[Diagnoser - Nemotron 3 Ultra]
    D -->|optional grounding| E[Tavily Search]
    E --> D
    D -->|root cause + patch proposal| F[Fixer]
    F -->|apply patch| C
    C -->|re-run benchmark| F
    F -->|verified result| G[Dashboard]
    B -->|timeline data| G
    D -->|diagnosis + diff| G
```

## 2. Component Breakdown

### 2.1 Bisector (Orchestrator)

- **Model**: Nemotron 3 Nano — cheap, fast, used purely for control-flow decisions (e.g., "is this score meaningfully worse than baseline?", "which half of the range to search next?").
- **Responsibility**: implement binary search over commit range. For each candidate commit:
  1. Checkout commit in a fresh Token Factory Sandbox
  2. Build/install dependencies (cache this step where possible to save time/credits)
  3. Run benchmark N times (recommend N=3–5), record median
  4. Compare to baseline threshold; narrow range accordingly
- **Output**: exact commit hash where regression was introduced, plus the full timeline of scores per commit for the dashboard.

### 2.2 Diagnoser

- **Model**: Nemotron 3 Ultra — long context window is the key asset here; feed it the full diff at the guilty commit plus surrounding file context (not just the diff hunk in isolation).
- **Responsibility**: classify root cause into a constrained taxonomy (extendable):
  - N+1 query / repeated DB or network calls in a loop
  - Lost or invalidated cache
  - Accidental algorithmic complexity increase (e.g., O(n) → O(n²))
  - New synchronous/blocking call on a hot path
  - Unnecessary object allocation / serialization overhead
- **Constraint**: diagnosis must cite the specific lines/diff hunk responsible — never allow a free-text-only answer with no grounding in the actual diff.
- **Tavily integration**: for the identified pattern, optionally search for "is this a known issue with [library/function]" to add a citation or confidence signal to the diagnosis. This is the basis for the Tavily bonus prize.

### 2.3 Fixer + Verifier

- **Model**: Nemotron 3 Ultra generates the patch; Nano can be used for cheaper sanity checks (does the patch even apply cleanly) before spending a full sandbox cycle.
- **Loop**:
  1. Generate patch based on diagnosis
  2. Apply in sandbox
  3. Run benchmark (same methodology as bisector — same N runs, same statistic)
  4. If recovered within threshold → success, stop
  5. If not → feed failure back to Ultra with the new benchmark result, retry (cap at 3 attempts)
- **Failure mode**: if unresolved after cap, report root cause + attempted patches transparently. Do not fabricate a "fixed" result.

### 2.4 Dashboard

- **Purpose**: this is the primary "Design" artifact judges will interact with/watch in the video.
- **Views**:
  - Timeline chart: benchmark score per commit (x-axis: commit index/date, y-axis: benchmark metric)
  - Click a point at/after the regression → side panel with: diagnosis text, cited diff hunk, before/after benchmark numbers, patch diff, Tavily citation if used
- **Tech**: React/Next.js recommended for a clean interactive chart (e.g., Recharts or D3); Streamlit is an acceptable faster-to-build fallback if timeline is tight, though it reads as less "designed."

## 3. Data Flow / Interfaces

```
POST /analyze
  { repo_url, benchmark_command, commit_range? }
  → returns job_id

GET /jobs/{job_id}
  → { status, timeline: [{commit, score, timestamp}], regression_commit,
      diagnosis: { category, explanation, cited_lines, tavily_refs },
      fix: { patch_diff, verified: bool, before_score, after_score } }
```

Keep this as a simple job-based API (even if backed by a single process for the hackathon) so the frontend can poll and render progressively — this matters for a good live demo if bisection takes more than a few seconds.

## 4. Sandbox Execution Design

- Each candidate commit gets a **fresh, isolated** Token Factory Sandbox run — no shared state between runs, to keep benchmark numbers comparable.
- Cache dependency installation layers where possible (e.g., reuse a base image/layer with dependencies pre-installed at a given lockfile hash) to keep iteration speed and credit usage reasonable — this matters a lot given a 5-week build with limited free credits.
- Benchmark script should be **deterministic and quick** (seconds, not minutes) — choose or write a benchmark that isolates the specific hot path you're demoing, not a full end-to-end test suite.
- Log raw scores per run (not just the median) so noise can be inspected/debugged.

## 5. Non-Functional Requirements

| Requirement | Target |
| --- | --- |
| Bisection reliability | Correctly identifies guilty commit despite ±10% benchmark noise |
| End-to-end time for a demo case | Under 3 minutes live, or pre-recorded/cached for the video if the real thing is slower |
| Cost control | Cache builds; use Nano wherever reasoning isn't required; avoid Ultra calls in tight loops |
| Reproducibility | A judge/reviewer can clone the repo and reproduce at least one demo case end-to-end following the README |

## 6. Tech Stack

- **Backend/orchestration**: Python (subprocess/sandbox control, benchmark parsing)
- **Model access**: Nebius Token Factory API (Nemotron 3 Nano + Ultra)
- **Sandbox execution**: Token Factory Sandboxes
- **Search grounding**: Tavily API
- **Frontend**: React/Next.js + a charting library (Recharts/D3), or Streamlit as a faster fallback
- **Version control inspection**: `git` CLI / GitPython for commit walking and diff extraction

## 7. Repo Structure (proposed)

```
/perf-regression-detective
├── README.md                 # setup, usage, Nemotron/Token Factory/Tavily callouts, license header
├── LICENSE                   # MIT or Apache 2.0, visible at top of repo
├── backend/
│   ├── bisector.py
│   ├── diagnoser.py
│   ├── fixer.py
│   ├── sandbox_client.py     # Token Factory Sandbox wrapper
│   ├── models.py             # Nemotron API wrapper (Nano/Ultra calls)
│   ├── tavily_client.py
│   └── api.py                # job endpoints
├── frontend/
│   └── ...                   # dashboard app
├── demo_cases/
│   ├── case_1_notes.md       # curated real regression + source (issue link, resolving PR)
│   ├── case_2_notes.md
│   └── case_3_notes.md
└── docs/
    ├── PRD.md
    └── TRD.md
```

## 8. Testing & Validation Plan

- Unit test the bisection logic against a synthetic mock commit history first (fast, no sandbox cost) before wiring in real sandboxes.
- Validate the diagnoser against the 2–3 curated real-world cases where the true root cause is already known (from the issue tracker/PR) — this is your ground truth for the demo and for confidence in judging.
- Stress-test benchmark noise handling by deliberately running a stable commit multiple times and confirming the system does not false-positive a regression.

## 9. Open Technical Questions to Resolve Early (Week 1)

1. Which language/ecosystem for the flagship demo repos (Python is recommended — fastest sandbox setup, huge pool of real OSS regressions to mine)?
2. Exact benchmark methodology: wall-clock time, throughput, or a custom scoring function? Pick one and be consistent.
3. What regression threshold counts as "real" vs noise (e.g., >15% slowdown across median of 5 runs)?
