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

- `/` — a real form that `POST`s to `/analyze` and redirects to `/job/{job_id}`.
- `/job/[jobId]` — polls `GET /jobs/{job_id}` every 2s (`lib/useJobPolling.ts`)
  until the job reaches `done` or `failed`. A transient fetch failure shows
  "Reconnecting…" without clearing the last known state; only a job that
  never loaded at all shows a hard error.
- `/dev/states` — the fixture harness described above. Not a hidden debug
  route; kept around as the fastest way to check a state that's easy to
  forget to test live (`failed`, `unresolved_diagnosis_only`).
- `lib/types.ts` mirrors `backend/api.py`'s Pydantic models field-for-field
  — that file is the source of truth here, not `docs/AGENT_SPECS.md` §4 or
  `docs/TRD.md` §3 (those two disagree with each other; the real API
  differs from both by adding `error`, used when `status: "failed"`).

## Fonts

Self-hosted via `@fontsource-variable/instrument-sans` and
`@fontsource-variable/jetbrains-mono` (imported in `app/globals.css`)
rather than `next/font/google` — no runtime request to Google's font CDN
on every page load, and no external network dependency at build time
either.

## Known contract gap — flagging, not guessing around

`Diagnosis` (`AGENT_SPECS.md` §2's output schema, `TRD.md` §3, and the real
`DiagnosisModel` in `backend/api.py`) has no field carrying the guilty
commit's diff text — only `cited_lines` (short strings). But
`BUILD_03_FRONTEND_DASHBOARD.md`'s drill-down panel asks for "the cited
diff hunk, render as an actual diff" as the panel's wow moment, which
needs that diff text somewhere in the API response.

Nothing currently provides it. `lib/types.ts` models `Diagnosis.diff` as
an **optional** field so the UI is ready the moment a backend owner adds
it — `DiagnosisSection` renders the full inline diff (via `DiffViewer`,
with `cited_lines` highlighted in Butter Yellow) when it's present, and
falls back to a plain highlighted list of the cited snippets when it
isn't. The `/dev/states` fixtures include the full diff so you can see
the intended experience either way.

**Suggested fix for whoever owns `backend/diagnoser.py` / `api.py`:** add
`diff: str` to `DiagnosisModel`, populated from the same diff
`diagnoser.py` already receives as input — it doesn't need a new fetch,
just returning what it was already given.

## Design notes

- Color tokens, font choices, and the "hairline rules instead of card
  stacks" structure are documented inline in `app/globals.css` and
  `components/ui.tsx`. Single light theme by design (the brief's palette
  — paper background, ink text — *is* the theme), not a light/dark pair.
- The diff viewer (`components/dashboard/DiffViewer.tsx`) is a small
  hand-rolled unified-diff parser (`lib/diff.ts`), not a dependency — the
  backend already hands back plain unified-diff strings, and the only
  extra behavior needed (matching `cited_lines` against real diff lines)
  is specific to this app.
- Motion is intentionally sparing: the regression marker's pulse, the
  drill-down panel's slide (ease-out in, ease-in out, right-drawer on
  desktop / bottom-sheet on mobile), and `useReducedMotion` gating on
  both. Everything else is a plain CSS transition or no motion at all.
