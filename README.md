# Culprit

**Finds the exact commit where your benchmark got slower, explains why, and proves the fix — and shows its work live.**

Built for the Nebius × NVIDIA Global AI Hackathon (Coding & Agentic Engineering track). Culprit bisects a repo's history in **Token Factory Sandboxes** to locate a performance regression, diagnoses the root cause with **Nemotron 3 Ultra** (grounded with **Tavily**), proposes a fix, and **re-runs the benchmark on the patched code** to prove it — all streamed to a dashboard with real numbers, not vibes.

> The headline claim is the boring one that matters: *a fix is only ever called "verified" if a sandbox re-run of the patched code actually came back within threshold.* Everything else is built around being able to prove that.

## See it in 60 seconds (no API keys)

```bash
# terminal 1 — backend
cd backend
pip install -r requirements.txt
CULPRIT_OFFLINE=1 python api.py

# terminal 2 — dashboard
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open http://localhost:3000 and press **Run the live demo**. In about a minute you'll watch a real git bisection of a 20-commit sample repo: each benchmark run landing as it happens, the search window collapsing onto the guilty commit, a diagnosis with cited lines, and a patch whose fix is verified by a real re-run (~297 ms → ~10 ms).

**Offline mode is honest about what it is.** Git, the sandbox subprocesses and every benchmark number are real. Only the three Nemotron *roles* are replaced by a small deterministic stand-in (`backend/offline.py`) — and every event, job record, page and PR report says so. It is never selected implicitly: a missing API key in normal mode is still a hard error.

## Running it for real (Nemotron + Token Factory)

```bash
cd backend
cp .env.example .env     # NEBIUS_API_KEY, CONTREE_PROJECT, CONTREE_IMAGE, TAVILY_API_KEY
python api.py
```

Then open **/new**: its *Backend ready* panel runs `GET /preflight`, which checks — against the **live Token Factory catalog** — that your Nemotron model IDs exist (and lists the real ones if not), that the API key is accepted, and that the sandbox project is set. Misconfiguration shows up *before* anything is spent, not three minutes into a run.

## What you get

| | |
|---|---|
| **Live terminal** | Every row is a real event: a git call, a sandbox run landing as a bar (colored against the real baseline/threshold), a Nemotron request — expand it for the exact system prompt, payload and **raw response**. In-flight operations are live spinners with timers that settle in place. Filter by search / models / sandbox / grounding / fix; download the full `.jsonl`. |
| **Bisect scanner** | One cell per commit. *Solid* = measured in a sandbox; *faint* = ruled out by reasoning only. The window of suspects visibly collapses. A cancelled search is never painted "all clean" and claims no speedup. |
| **Evidence chart** | Baseline, the regression-threshold band, **every raw run** behind each median, dashed lines across unmeasured gaps, and the verified fix landing back in the band. |
| **Diagnosis** | Root-cause category with cited lines — each citation is verified against the real diff (`citation.check` events); unverifiable claims are downgraded to "other" rather than shipped. Tavily sources show their relevance score and snippet. |
| **Under the hood** | Per-model calls, latency, tokens (from the API's own `usage`, never estimated), sandbox runs, Tavily searches — summed from the event stream. |
| **Export** | A PR-ready write-up (`/jobs/{id}/report.md`) and a real `.patch` (`/jobs/{id}/fix.patch`). The report can only claim what the job proved. |
| **History, cancel, persistence** | Jobs and their full event logs are stored in SQLite; cancel stops cleanly at the next checkpoint. |

## How it uses Nemotron, Token Factory and Tavily

- **Nemotron 3 Nano — control flow.** For every benchmarked commit it returns `clean` / `regressed` / `inconclusive (run more)`. This is the high-frequency, latency-sensitive judgement, so the small model gets it. Its verdict is cross-checked against plain median-vs-threshold arithmetic; a contradiction is overridden **and shown** in the terminal (`arithmetic override`).
- **Nemotron 3 Ultra — reasoning.** Two steps only: the *Diagnoser* (root cause, with lines it must cite from the diff) and the *Fixer* (exact-text edits, converted to a real unified diff by `patcher.py`). In a typical run Nano makes ~75% of model calls; the large model is spent only where reasoning is needed.
- **Token Factory Sandboxes — real measurement.** Each candidate commit runs in a fresh, disposable, VM-isolated sandbox, so benchmarks can't contaminate each other and untrusted benchmark commands can't touch your machine. The patched code is verified the same way.
- **Tavily — grounding.** After a diagnosis, Culprit searches for the category + cited pattern and attaches sources with Tavily's own relevance score. Results below a floor are dropped — *"if Tavily returns nothing relevant, don't force a citation"*. The query, sources, scores and drop count are all visible in the terminal.

## Architecture

```
                         ┌────────────────────────── dashboard (Next.js) ──────────────────────────┐
                         │  scanner · evidence chart · live terminal · diagnosis · export · history │
                         └───────────▲───────────────────────────────▲──────────────────────────────┘
                       GET /jobs/{id} (state)            GET /jobs/{id}/events?after=seq (live stream)
                         ┌───────────┴───────────────────────────────┴──────────────┐
                         │  api.py — FastAPI · job runtime · SQLite · cancel · limits│
                         └───┬───────────────────────────────────────────────────┬───┘
                             │ events.py: every action → append-only event       │
      ┌──────────────────────┼───────────────┬──────────────────┬────────────────┘
      ▼                      ▼               ▼                  ▼
  bisector.py           diagnoser.py     fixer.py         sandbox_client.py
  Nano per commit       Ultra + citation  Ultra edits →    Token Factory VM  ─ or ─
  log₂(n) probes        check + Tavily    patcher.py →     local git worktree
                                          real re-run
```

## Honesty guarantees (AGENTS.md rule 1, enforced in code)

- Benchmark numbers only ever come from real runs. "Verified" mirrors a real sandbox verdict and nothing else.
- A fix that doesn't resolve the regression is reported as **unresolved**, with all attempts — never dressed up.
- A patch that *crashes* is a failed attempt whose traceback is fed back to the model; it is shown with **no score**, because none was measured.
- A diagnosis whose cited lines aren't in the diff is retried once, then downgraded to `other`.
- Infrastructure failures fail the job loudly. Nothing is silently skipped.

## API

| | |
|---|---|
| `POST /analyze` | `{repo_url, benchmark_command, commit_range?}` → `{job_id}`. Omit the range to search the last 30 commits; ends may be SHAs, tags or branches. |
| `POST /demo` | start a job on the bundled sample repo |
| `GET /jobs` · `GET /jobs/{id}` | history · full state (+ derived `metrics`) |
| `GET /jobs/{id}/events?after=` | live activity stream, cursor-paged |
| `POST /jobs/{id}/cancel` | cooperative cancel |
| `GET /jobs/{id}/report.md` · `/fix.patch` | PR write-up · patch file |
| `GET /config` · `/preflight` · `/health` | mode · readiness checks · liveness |

Schema and event vocabulary: `docs/AGENT_SPECS.md` §4–§6 · wire models: `backend/schema.py`.

## Tests

```bash
cd backend  && python -m pytest          # 161 tests — real git repos and real sandbox subprocesses; only the model roles are faked
cd frontend && npm test && npm run lint && npm run build
```

The frontend's unit tests run against a **job and event stream captured from the real pipeline** (`frontend/tests/fixtures/`), so they fail if the backend's actual output drifts from what the UI expects.

## Known limitations (read these before trusting a result)

- **The live Nebius path has not been exercised against a real account in this repo's CI.** It is covered by unit tests with mocked HTTP (Token Factory sandbox API, Nemotron calls, preflight) and verified against Nebius's published docs, but model IDs and sandbox response shapes are exactly what `GET /preflight` exists to confirm on first run.
- Bisection assumes **one monotonic regression** (clean…clean, regressed…regressed). Flaky or non-monotonic histories can mislead it; the scanner labels unmeasured commits as *inferred* for this reason.
- A commit where the benchmark itself can't run fails the job (no `git bisect skip` yet).
- Benchmark noise is handled with repeated runs and a Nano/arithmetic cross-check, not eliminated; very small regressions near the threshold need more runs.
- The **local sandbox executes benchmark commands on the host**. It's for development and offline demos only — don't expose it to untrusted input. Remote URLs are restricted to https/ssh and git option-injection is blocked, but there is **no authentication or rate limiting**: put a demo deployment behind your own.
- `demo_cases/` still needs the curated real-OSS regressions (with verified ground truth) described in `docs/build-prompts/BUILD_04_DEMO_AND_SUBMISSION.md`. The bundled sample repo is a real git history with a real, measured regression, but it is *constructed* — it is a reproducible smoke test, not a substitute.

## Repo layout

```
backend/    api.py (HTTP + job runtime) · schema.py (wire models + SQLite stores) · events.py (live stream)
            bisector.py · diagnoser.py · fixer.py · patcher.py · tavily_client.py · models.py · sandbox_client.py
            preflight.py · report.py · offline.py · demo_repo.py · tests/
frontend/   Next.js dashboard · src/lib/terminal.ts + scanner.ts (pure, tested) · tests/
docs/       PRD · TRD · AGENT_SPECS (kept in sync with the code) · PROJECT_PLAN · build-prompts/ · SUBMISSION.md
demo_cases/ real-OSS ground-truth cases (to be filled — see Known limitations)
```

If you're an AI coding agent working on this repo, read `AGENTS.md` first.

## License

MIT — see `LICENSE`.
