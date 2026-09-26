# Project Plan & Submission Checklist

## Performance Regression Detective — Nebius x NVIDIA Global AI Hackathon

**Deadline: Oct 30, 2026, 10:00am PDT**

---

## 1. Five-Week Timeline

### Week 1 — Foundation (no AI yet)

- Grab Token Factory credits (promo form + Builders Program, $50 total) and Tavily credits
- Pick the language/ecosystem for demo repos (recommend Python)
- Get the sandbox execution loop rock solid: checkout a commit → run a benchmark → get a number back, reliably, with noise handled
- Source 2–3 real historical performance regressions from real OSS repos (search issue trackers for "performance regression" + a resolving PR) — this is your ground truth
- **Exit criteria**: you can manually reproduce one known regression's before/after numbers via script, without any model in the loop yet

### Week 2 — Core Agent Loop

- Build the Bisector (binary search over commit history using the Week 1 sandbox loop)
- Wire up Nemotron 3 Ultra for the Diagnoser on your first curated case
- Get one full case working end-to-end, even if ugly: repo in → guilty commit + explanation out
- **Exit criteria**: one real case fully diagnosed correctly, root cause matches known ground truth

### Week 3 — Fix Loop + Grounding

- Build the Fixer + Verifier loop (patch → sandbox re-run → check recovery)
- Integrate Tavily for root-cause grounding/citations
- Run all 2–3 curated cases through the full pipeline; fix bugs and edge cases (flaky benchmarks, ambiguous diffs)
- **Exit criteria**: at least 2 of 3 cases produce a verified fix; the third at minimum produces a correct diagnosis

### Week 4 — Dashboard & Design Polish

- Build the timeline chart + drill-down panel (this is your "Design" judging score — invest real time here)
- Make the whole flow presentable: clear states for "running," "found regression," "diagnosed," "fixed/verified"
- Start drafting the README (setup, Nemotron/Token Factory/Tavily callouts, license)
- **Exit criteria**: a stranger could open the dashboard and understand what happened without you narrating

### Week 5 — Demo, Polish, Submit Early

- Record the demo video (see script outline below), aim for ≤3 minutes
- Finalize README, add LICENSE at top of repo
- Deploy/host a working demo URL (or clearly document a test build)
- Write the project description (what/why/how) for the submission form
- Fill out the Nebius/NVIDIA feedback field genuinely — there's a $100 x 10 "Most Valuable Feedback" prize
- **Submit at least 2–3 days before the deadline**, not at the wire — buffer for platform issues
- If a Builders & Brews event is near you before the deadline, attend for a shot at the $500 City Winner Award

## 2. Submission Checklist (mapped to official rules)

| Requirement | Status | Notes |
| --- | --- | --- |
| Runs on Nebius Token Factory or AI Cloud | ☐ |  |
| Uses ≥1 NVIDIA open-source model | ☐ | Nemotron 3 Nano + Ultra |
| Category selected | ☐ | Coding & Agentic Engineering |
| Project description (what/why/how) | ☐ |  |
| Working demo URL | ☐ | Not required only for Physical AI — required here |
| Demo video ≤3 min, public YouTube, narrated | ☐ | Must cover how Token Factory + Nemotron were used |
| Public repo (GitHub/GitLab/Bitbucket) | ☐ |  |
| OSS license visible at top of repo | ☐ | MIT, Apache 2.0, or MPL 2.0 |
| README with setup instructions | ☐ |  |
| README highlights Nemotron/Token Factory/Nebius tool usage | ☐ |  |
| Feedback on Token Factory/AI Cloud/NVIDIA tools submitted | ☐ |  |
| (If applicable) City selection for Builders & Brews | ☐ |  |

## 3. Demo Video Script Outline (≤3 minutes)

1. **0:00–0:20 — The hook**: state the problem in one sentence ("A single commit can quietly double your app's latency, and finding it usually means hours of manual `git bisect`"). Show a real benchmark timeline with a visible cliff.
2. **0:20–1:00 — The bisection**: show the agent narrowing down to the exact guilty commit, running in Token Factory Sandboxes. Narrate briefly how Nemotron Nano handles the control-flow here.
3. **1:00–2:00 — The diagnosis**: show Nemotron 3 Ultra's explanation, the cited diff lines, and (if used) the Tavily-grounded confirmation. This is the "wow" moment — make the explanation legible on screen.
4. **2:00–2:40 — The fix**: show the proposed patch and the benchmark number recovering, verified live in a sandbox. Numbers on screen, before/after.
5. **2:40–3:00 — Close**: one sentence on why this matters for real teams, and a shot of the finished dashboard.

## 4. Team Roles (fill in once team is finalized)

| Role | Owner | Focus |
| --- | --- | --- |
| Sandbox/infra |  | Week 1 foundation, benchmark reliability |
| Agent/model logic |  | Bisector, Diagnoser, Fixer prompts and orchestration |
| Frontend/dashboard |  | Design-criterion work, Week 4 |
| Demo/storytelling |  | Curating cases, video script, README |

## 5. Buffer & Risk Notes

- Treat Week 5 as polish + submission, not new feature development. If the Fixer loop isn't reliable by end of Week 3, cut it back to "diagnosis only" rather than risk a broken demo — a correct diagnosis without a fix is still a strong, honest submission.
- Keep a running list of "known-good" demo cases from Week 1 onward so you always have a fallback if a live demo misbehaves.
