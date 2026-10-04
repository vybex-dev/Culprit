// FILE: frontend/src/lib/scanner.ts
//
// Pure logic behind the bisect scanner: given the commit list, the probes that
// have actually been benchmarked, and the current search window, decide what
// state each commit is in.
//
// The distinction that keeps this honest: a commit is only ever "clean" or
// "regressed" if a sandbox actually measured it. Everything else is either
// still a "suspect" (inside the window) or "inferred" — ruled out purely
// because bisection assumes ONE monotonic regression (clean…clean, then
// regressed…regressed). The UI draws inferred cells differently from measured
// ones and says so in its legend; it never presents a guess as a measurement.
//
// Only `import type` + relative `.ts` imports, so Node can run it under
// `node --test` directly.

import type { Commit, Probe } from "./types.ts";

export type CellState =
  | "baseline" // measured: the known-good starting point
  | "clean" // measured: within threshold of baseline
  | "regressed" // measured: beyond threshold
  | "guilty" // the first regressed commit — the answer
  | "suspect" // inside the search window, not yet measured
  | "inferred-clean" // before a clean probe: assumed clean (monotonic assumption)
  | "inferred-regressed"; // after a regressed probe: assumed regressed

export interface Cell {
  index: number;
  sha: string;
  subject: string;
  author: string;
  date: string;
  state: CellState;
  /** The probe that measured this commit, if any. */
  probe: Probe | null;
}

export interface ScannerStats {
  /** Commits benchmarked after the baseline. */
  probesUsed: number;
  /** Runs a linear scan would need (every commit after the start). */
  linearRuns: number;
  /** linear / probes — how many times fewer benchmarked commits than a linear scan. */
  speedup: number | null;
  converged: boolean;
}

/**
 * `completedClean` is true ONLY when the job ran to completion and found no
 * regression (so the unmeasured commits really were ruled out). A cancelled or
 * failed job has proved nothing about the commits it never reached — those stay
 * "suspect", not "inferred clean".
 */
export function buildCells(
  commits: Commit[],
  probes: Probe[],
  window: [number, number] | null | undefined,
  regressionSha: string | null,
  completedClean: boolean,
): Cell[] {
  const byIndex = new Map<number, Probe>();
  for (const p of probes) byIndex.set(p.index, p);

  const n = commits.length;
  // Before the endpoint probe reports a window, everything after the start is
  // still a suspect.
  const [lo, hi] = window ?? [1, n - 1];

  return commits.map((c): Cell => {
    const probe = byIndex.get(c.index) ?? null;
    let state: CellState;
    if (regressionSha && c.sha === regressionSha) state = "guilty";
    else if (probe) state = probe.verdict === "baseline" ? "baseline" : probe.verdict === "regressed" ? "regressed" : "clean";
    else if (c.index === 0) state = "baseline";
    else if (completedClean) state = "inferred-clean"; // ran to completion, nothing regressed
    else if (c.index < lo) state = "inferred-clean";
    else if (c.index > hi) state = "inferred-regressed";
    else state = "suspect";
    return { index: c.index, sha: c.sha, subject: c.subject, author: c.author, date: c.date, state, probe };
  });
}

/** `searchComplete`: the search actually ran to its answer (job "done" or a
 * guilty commit found) — NOT merely "the job stopped". A cancelled search
 * claims no speedup because it found nothing. */
export function scannerStats(commits: Commit[], probes: Probe[], regressionSha: string | null, searchComplete: boolean): ScannerStats {
  const probesUsed = probes.filter((p) => p.role !== "baseline").length;
  const linearRuns = Math.max(commits.length - 1, 0);
  return {
    probesUsed,
    linearRuns,
    speedup: probesUsed > 0 && linearRuns > 0 ? linearRuns / probesUsed : null,
    converged: searchComplete || regressionSha !== null,
  };
}

/** Human label for a cell state — used for tooltips and the legend. */
export const STATE_LABEL: Record<CellState, string> = {
  baseline: "known good (measured)",
  clean: "clean (measured)",
  regressed: "regressed (measured)",
  guilty: "guilty commit",
  suspect: "still a suspect",
  "inferred-clean": "assumed clean — before a clean probe",
  "inferred-regressed": "assumed regressed — after a regressed probe",
};
