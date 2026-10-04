<!-- FILE: frontend/README.md — place at this path in the Culprit repo -->
# frontend/ — Timeline Dashboard

Next.js 16 (App Router, TypeScript, Tailwind v4) dashboard for
`backend/api.py`'s job endpoints. Built against
`docs/build-prompts/BUILD_03_FRONTEND_DASHBOARD.md`.

## Setup

```bash
cd frontend
npm install
cp .env.example .env.local     # point NEXT_PUBLIC_API_BASE_URL at your backend
npm run dev                    # http://localhost:3000
```

No backend running yet? Visit `/dev/states` — it renders the dashboard
against static fixtures covering all eight states (queued, bisecting,
regression found, diagnosing, fixing, done/resolved, done/unresolved,
failed) with no network calls at all.

```bash
npm run build   # production build + typecheck
npm run lint
```

## How it's wired

- `/` — landing page. "Run the live demo" starts a real job on the bundled
  sample repo. (The hero's trace and numbers are a *recorded fixture* and are
  captioned as such — never presented as a live run.)
- `/new` — start an analysis: a one-click demo, or your own repo + benchmark
  command (commit range optional). A **preflight panel** asks the backend
  whether it's actually ready — API key present, Nemotron model IDs real in
  the live catalog, sandbox configured — *before* anything is spent.
- `/job/[jobId]` — the dashboard. Two feeds: `GET /jobs/{id}` is polled
  (`lib/useJobPolling.ts`) for state, and `GET /jobs/{id}/events?after=<seq>`
  is cursor-followed (`lib/useJobEvents.ts`) for the live terminal. Commits,
  probes and fix attempts are persisted as they happen, so the scanner and
  chart fill in live. A transient failure shows "Reconnecting…" without
  clearing the last known state.
- `/jobs` — history of every analysis (persisted by the backend).
- `/dev/states` — the fixture harness: all dashboard states against static
  data with no network. Kept as the fastest way to check states that are easy
  to forget (`failed`, `cancelled`, `unresolved_diagnosis_only`).

### The live terminal (`components/dashboard/Terminal.tsx`)

Every row is a **real event** from the pipeline. `lib/terminal.ts` (pure,
unit-tested against a stream captured from the real backend) turns events into
rows: spans (`probe.start`…`probe.end`, model calls, Tavily searches, fix
attempts) become one row with a live spinner + timer that settles in place;
individual `sandbox.run` events fold into one row of bars colored against the
real baseline and threshold; model calls expand to the exact request and raw
response; citation checks, Tavily sources and patches expand inline. Anything
left open when a job ends is marked *interrupted*, never an eternal spinner.

### Other dashboard pieces

- **Bisect scanner** — one cell per commit. *Solid* = measured in a sandbox;
  *faint* = ruled out by reasoning only. A cancelled search is never painted
  as "all clean" and claims no speedup (`lib/scanner.ts`, tested).
- **Evidence chart** — custom SVG: real baseline, the regression-threshold
  band, every raw run behind each median, dashed lines across unmeasured gaps,
  and the verified fix landing back inside the band. Linear/log toggle.
- **Under the hood** — per-model calls, latency, tokens (from the API's own
  `usage`, never estimated), sandbox runs, Tavily searches.
- **Export** — a PR-ready write-up and a real `.patch` file.

`lib/types.ts` mirrors `backend/schema.py` field-for-field (that file is the
source of truth). Every field added since the original contract is optional,
so fixtures and older backends still render.

## Tests

```bash
npm test          # node --test: terminal + scanner logic, against a REAL captured event stream
npm run typecheck
npm run lint
npm run build
```

The fixtures in `tests/fixtures/` are a job and event stream captured from the
actual pipeline (not hand-written), so these tests fail if the backend's real
output drifts from what the UI expects.

## Fonts

Self-hosted via `@fontsource-variable/instrument-sans` and
`@fontsource-variable/jetbrains-mono` (imported in `app/globals.css`)
rather than `next/font/google` — no runtime request to Google's font CDN
on every page load, and no external network dependency at build time
either.

## Contract notes

- `Diagnosis.diff` — the guilty commit's diff — **is** returned by the backend
  (`DiagnosisModel.diff`), populated from the same diff the Diagnoser received.
  The drill-down renders it inline with `cited_lines` highlighted; if it's ever
  absent it falls back to a highlighted list of the cited snippets.
- `FixAttempt.error` marks an attempt that crashed or didn't apply; its
  `score_after` is then a placeholder and the UI shows no number for it.

## Design notes

- Color tokens, font choices, and the "hairline rules instead of card
  stacks" structure are documented inline in `app/globals.css` and
  `components/ui.tsx`. Light, dark and system themes (toggle in the header);
  the terminal is a fixed dark console surface in both.
- The diff viewer (`components/dashboard/DiffViewer.tsx`) is a small
  hand-rolled unified-diff parser (`lib/diff.ts`), not a dependency — the
  backend already hands back plain unified-diff strings, and the only
  extra behavior needed (matching `cited_lines` against real diff lines)
  is specific to this app.
- Motion is intentionally sparing: the regression marker's pulse, the
  drill-down panel's slide (ease-out in, ease-in out, right-drawer on
  desktop / bottom-sheet on mobile), and `useReducedMotion` gating on
  both. Everything else is a plain CSS transition or no motion at all.
