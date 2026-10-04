# Submission kit (Devpost)

> Draft text + a video script tied to what the product really does. **Confirm the exact required fields on the hackathon's Devpost page** — this file doesn't assume them. Items marked ☐ need a human with real credentials.

## One-liner

Culprit finds the exact commit where your benchmark got slower, explains why with cited lines, and *proves* the fix with a real re-run — streaming every git call, sandbox run and Nemotron reply as it happens.

## Description

**The problem.** Performance regressions hide in innocent-looking commits ("simplify loading"). Finding them means bisecting by hand and benchmarking each candidate; fixing them means trusting a guess.

**What it does.** Give Culprit a repo and a benchmark command. It binary-searches the commit history — each candidate in a fresh, isolated **Token Factory Sandbox** — using **Nemotron 3 Nano** to judge every result (clean / regressed / run it again). **Nemotron 3 Ultra** then diagnoses the guilty diff, citing the exact lines (each citation is checked against the real diff), grounded with **Tavily**. It proposes a patch and **re-runs the benchmark on the patched code**. "Verified" appears only when that re-run came back within threshold.

**Why the routing matters.** The small model makes ~75% of the model calls (the high-frequency judgements); the large model is spent only on the two steps that need real reasoning. The dashboard's *Under the hood* panel shows the split from real usage data.

**What's different.** Most agent demos show an answer. Culprit shows its *work*: a live terminal where every row is a real event (expand any model call for the exact request and raw response), a scanner that distinguishes commits it *measured* from commits it merely *ruled out*, and a chart of every raw run against the real threshold. It also tells you when it fails: a patch that crashes is shown with no score; an unfixed regression is reported as unresolved.

## Mapping to the judging criteria

| Criterion | Where to look |
|---|---|
| **Technological implementation** | Nano/Ultra tiering with arithmetic cross-check of Nano; citation verification; exact-text edits → real diff (`patcher.py`); crash-tolerant fix loop with feedback; endpoint-confirmed bisection (`git bisect` semantics); cooperative cancel; SQLite persistence; live event stream; preflight against the live model catalog; 161 backend tests on real repos/subprocesses |
| **Design** | Scanner, evidence chart, live terminal, drill-down with the real cited diff, export, history; light/dark/mobile; honest empty/failed/cancelled states |
| **Impact** | Regressions cost real money and time; the PR report + `.patch` drop straight into a review |
| **Idea** | "Prove it with a re-run" as the product's core invariant |
| **Track: Coding & Agentic Engineering** | Agents that write, run and test code in Token Factory Sandboxes — the fix is only accepted after the sandbox runs it |
| **Best use of Tavily** | Query + sources + relevance scores + a relevance floor ("don't force a citation"), all visible in the terminal |

## ~3-minute video script

1. **0:00 — the pain (15s).** A commit titled "refactor: simplify order summary loading". Benchmark: 10 ms → 300 ms. Which commit?
2. **0:15 — start (10s).** /new → the *Backend ready* panel (model IDs verified against the live catalog) → Run.
3. **0:25 — the search (45s).** Scanner + terminal side by side. Point at a probe spinning, its runs landing as bars (red = over threshold), the window collapsing, "7 commits benchmarked out of 19".
4. **1:10 — the judgement (25s).** Expand a Nano call: exact request, raw JSON reply. Show an *arithmetic override* if one occurred (a feature, not a bug).
5. **1:35 — the diagnosis (30s).** Drill-down: N+1, the cited lines highlighted in the real diff; `citation.check ✓ in diff`; the Tavily sources with scores.
6. **2:05 — the proof (30s).** Fix attempt → patched runs → "verified" → chart diamond lands back in the band. Mention a crashed attempt would show *no score*.
7. **2:35 — take it with you (15s).** Copy PR description / download `.patch`. Under the hood: models, tokens, sandboxes.
8. **2:50 — close (10s).** "It doesn't just claim a fix — it proves it."

**Record the video against a real Nemotron + Token Factory run, not offline mode.** Offline mode is for development and for judges to reproduce the pipeline without keys; the dashboard says so on every page.

## Before you submit

- ☐ Run with real keys; open /new and resolve anything `GET /preflight` flags (especially the Nano model ID — Nebius lists several spellings).
- ☐ Run the bundled demo **and** at least one real OSS case live; fill `demo_cases/` with verified ground truth (SHA, numbers, reproduction steps).
- ☐ Replace any sample numbers in the README/video with numbers from your recorded run.
- ☐ Rotate/remove any keys; confirm `.env` is not committed.
- ☐ Make the repo public; confirm the license; add the video link.
- ☐ Decide the product name (README still says "Culprit" as a working name).
- ☐ If hosting a public demo: put it behind auth/rate limits and use the Token Factory sandbox backend (never the local one).
