# Build Prompt — Frontend: Timeline Dashboard

**You are building the single artifact judges will actually look at.** Per `AGENTS.md`'s Dashboard definition of done: *a person with no prior context can look at it for 60 seconds and correctly explain what regressed, why, and whether it was fixed.* This is also the "Design" judging criterion made concrete, and it's on-camera for the 2:00–3:00 stretch of your demo video (`PROJECT_PLAN.md §3`). Take it seriously, but don't over-invest — `AGENTS.md` explicitly warns against "a beautiful dashboard around a fake/mocked backend"; build against the real API contract from day one, even with mock data at first.

## Read first

- `TRD.md §2.4` (Dashboard), `§3` (Data Flow/Interfaces)
- `BUILD_00_OVERVIEW.md` — canonical job-state schema (this is what you're rendering)
- `PRD.md §10` (demo requirements — a judge must understand this without narration)
- `PROJECT_PLAN.md §3` (video script — the diagnosis panel is the "wow" moment, make it legible on a recorded screen)

## Install first

```bash
npx skills@latest add emilkowalski/skills
```

This installs a small collection built by a design engineer who's worked on Vercel and Linear's interfaces, aimed specifically at the mistakes agents make with animation and interface polish (wrong easing direction, solid borders instead of soft shadows, motion where none was needed). Use the pieces as follows:

- **`emil-design-eng`** — the main skill (animation fundamentals + general design taste). Keep this active for the whole build.
- **`find-animation-opportunities`** — run this *after* your static UI works, to get a short, high-conviction list of where motion actually earns its place (it's deliberately restrained — expect it to reject most candidates). Don't skip straight to animating everything.
- **`review-animations`** — run this as a final pass before calling the stack done.
- The repo is actively growing ("more coming soon" per its README) — check `github.com/emilkowalski/skills` for anything added since this was written.

## Color system

You were given two brand colors: **Butter Yellow `#FFF275`** and **Royal Iris `#3A0CA3`**. Two colors alone can't carry an entire dashboard (you need neutrals for text/backgrounds, and you need a way to signal "this is bad news" vs. "this is good news" that doesn't rely on people already knowing your brand). Here's a full palette derived from your two, with the additions clearly marked — swap the additions if you'd rather stay strictly two-color:

```css
:root {
  /* given */
  --butter: #FFF275;      /* warm accent — draws the eye to the exact regression point on the timeline, tooltips, highlights */
  --iris: #3A0CA3;        /* primary brand — header/nav, primary buttons, focus rings, links */

  /* derived neutrals (not given — proposed) */
  --ink: #1A1523;         /* near-black with a plum undertone, for body text — pairs with iris rather than a flat gray */
  --paper: #FFFDF7;       /* warm off-white background, pairs with the butter undertone */
  --iris-100: #EDE4FB;    /* light iris tint — subtle backgrounds, hover states, chart gridlines */
  --iris-700: #2A0878;    /* darker iris — text on yellow surfaces, borders */
  --butter-700: #E6D948;  /* darker butter — borders/hover states on yellow surfaces */

  /* semantic (not given — proposed, use sparingly, small badges/icons only) */
  --resolved: #2D9A5B;    /* muted moss green — "fix verified" badge only */
  --unresolved: #E1483D;  /* muted coral-red — "regression"/"unresolved" badge only */
}
```

Usage split: Iris + Butter + the neutrals should carry ~90% of the interface (chrome, chart lines, the regression-point highlight). The two semantic colors exist only for the handful of places where the dashboard needs to say "good" or "bad" at a glance without the viewer having learned your brand first — a small dot or badge next to `fix.verified`, not a wash of red/green across the whole screen.

## Data contract

Poll `GET /jobs/{job_id}` (see `BUILD_00_OVERVIEW.md` for the full canonical schema) and render progressively — don't wait for `status: "done"` to show anything. Build your first pass against a **static fixture file** matching that exact schema so you're not blocked on the backend stack being finished; wire up real polling once both stacks exist.

State → UI mapping:

| `status` | What the dashboard shows |
|---|---|
| `queued` | "Starting up" — no chart yet, or an empty timeline shell |
| `bisecting` | Timeline chart filling in as commits get scored, no regression marker yet |
| (regression found) | `regression_commit` is non-null — highlight that point on the timeline in **Butter Yellow**, still while `status` may be `bisecting` or `diagnosing` |
| `diagnosing` | Regression point clickable → side panel shows a loading state, then `diagnosis` once populated |
| `fixing` | Side panel shows `fix_attempts` accumulating (including failed ones — don't hide them) |
| `done` | `final_result` badge: green if `resolved`, neutral/amber if `unresolved_diagnosis_only` — **never** show a resolved badge unless `fix.verified === true` |
| `failed` | Clear error state, not a silent blank screen |

## Views (from `TRD.md §2.4`)

1. **Timeline chart**: benchmark score per commit. X-axis: commit index or date. Y-axis: benchmark metric. Recharts or D3 both work — Recharts is faster to get right for a hackathon timeline, reach for D3 only if you need an interaction Recharts can't do cleanly.
2. **Drill-down side panel**, opened by clicking the regression point: diagnosis text, the cited diff hunk (render as an actual diff, not a plain paragraph — this is the "wow" moment from the video script), before/after numbers, the patch diff, and the Tavily citation if one exists.

## Motion, following Emil's philosophy

- Correct easing direction: ease-out for things entering (side panel opening), ease-in for things leaving.
- Respect `prefers-reduced-motion`.
- Cap yourself at roughly 5–7 animated moments for the whole app — the chart-point-selection expand and the state-transition reveals (diagnosis appearing once ready) are the obvious candidates; resist animating every status change just because you can.
- Motion should follow structure, not lead it — get the static states fully correct first, then layer animation on top, then run `review-animations` as your last step before calling this stack done.

## Build order

1. Static UI against the fixture data, covering every row of the state table above — including `failed` and `unresolved_diagnosis_only`, which are easy to forget and easy to fake-demo around.
2. Wire up real polling against the backend's `GET /jobs/{job_id}`.
3. Motion pass: `find-animation-opportunities` → implement the short list it gives you → `review-animations` to check the result.
4. Legibility pass for the recorded video specifically — desktop-first, make sure text and the diff view are readable at whatever resolution you'll be screen-recording at, not just on your own monitor.

## Definition of done

A stranger could open the dashboard and understand what happened without you narrating it.

## Hand back

A short walkthrough (or a recording) of the dashboard against at least one real end-to-end job — showing the `bisecting → regression found → diagnosing → fixing → done` sequence, plus what the `failed` and `unresolved_diagnosis_only` states look like.
