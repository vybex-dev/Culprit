import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { buildCells, scannerStats } from "../src/lib/scanner.ts";
import type { Commit, Probe } from "../src/lib/types.ts";

const job = JSON.parse(readFileSync(new URL("./fixtures/demo-job.json", import.meta.url), "utf8")) as {
  commits: Commit[]; probes: Probe[]; window: [number, number]; regression_commit: string;
};

test("the guilty commit is marked, and every measured commit shows its measured verdict", () => {
  const cells = buildCells(job.commits, job.probes, job.window, job.regression_commit, false);
  assert.equal(cells.length, job.commits.length);
  assert.equal(cells.filter((c) => c.state === "guilty").length, 1);
  assert.equal(cells.find((c) => c.state === "guilty")!.sha, job.regression_commit);
  assert.equal(cells[0].state, "baseline");
  for (const p of job.probes.filter((p) => p.verdict === "clean")) assert.equal(cells[p.index].state, "clean");
  for (const p of job.probes.filter((p) => p.verdict === "regressed" && p.commit !== job.regression_commit)) {
    assert.equal(cells[p.index].state, "regressed");
  }
});

test("unmeasured commits are 'inferred', never presented as measured", () => {
  const cells = buildCells(job.commits, job.probes, job.window, job.regression_commit, false);
  const measured = new Set(job.probes.map((p) => p.index));
  const guiltyIdx = cells.find((c) => c.state === "guilty")!.index;
  for (const c of cells) {
    if (measured.has(c.index) || c.state === "guilty") continue;
    assert.equal(c.probe, null);
    assert.equal(c.state, c.index < guiltyIdx ? "inferred-clean" : "inferred-regressed");
  }
});

test("mid-search, the window's interior is 'suspect'", () => {
  const commits: Commit[] = Array.from({ length: 10 }, (_, i) => ({ index: i, sha: `s${i}`, subject: "", author: "", date: "" }));
  const probe = (index: number, verdict: string): Probe => ({
    step: index, commit: `s${index}`, index, role: index === 0 ? "baseline" : "bisect", raw_scores: [1], median_score: 1,
    pct_change: 0, verdict, rounds: 1, nano: [], timestamp: "", wall_s: 0,
  });
  const cells = buildCells(commits, [probe(0, "baseline"), probe(9, "regressed"), probe(4, "clean")], [5, 8], null, false);
  assert.deepEqual(cells.map((c) => c.state), [
    "baseline", "inferred-clean", "inferred-clean", "inferred-clean", "clean",
    "suspect", "suspect", "suspect", "suspect", "regressed",
  ]);
});

test("before any window is known, everything after the start is a suspect", () => {
  const commits: Commit[] = Array.from({ length: 4 }, (_, i) => ({ index: i, sha: `s${i}`, subject: "", author: "", date: "" }));
  const cells = buildCells(commits, [], null, null, false);
  assert.deepEqual(cells.map((c) => c.state), ["baseline", "suspect", "suspect", "suspect"]);
});

test("a finished job with no regression infers the rest clean", () => {
  const commits: Commit[] = Array.from({ length: 4 }, (_, i) => ({ index: i, sha: `s${i}`, subject: "", author: "", date: "" }));
  const cells = buildCells(commits, [], null, null, true);
  assert.deepEqual(cells.map((c) => c.state), ["baseline", "inferred-clean", "inferred-clean", "inferred-clean"]);
});

test("stats: real probe count vs a linear scan", () => {
  const s = scannerStats(job.commits, job.probes, job.regression_commit, true);
  assert.equal(s.probesUsed, job.probes.length - 1);
  assert.equal(s.linearRuns, job.commits.length - 1);
  assert.ok(s.speedup! > 2.5 && s.converged);
  assert.equal(scannerStats(job.commits, [], null, false).speedup, null);
});

test("a CANCELLED/failed search never turns unmeasured commits into 'inferred clean'", () => {
  const commits: Commit[] = Array.from({ length: 5 }, (_, i) => ({ index: i, sha: `s${i}`, subject: "", author: "", date: "" }));
  const probe = (index: number, verdict: string): Probe => ({
    step: index, commit: `s${index}`, index, role: index === 0 ? "baseline" : "endpoint", raw_scores: [1], median_score: 1,
    pct_change: 0, verdict, rounds: 1, nano: [], timestamp: "", wall_s: 0,
  });
  // job stopped after baseline + HEAD probe; the window was still [1, 4]
  const cells = buildCells(commits, [probe(0, "baseline"), probe(4, "regressed")], [1, 4], null, false);
  assert.deepEqual(cells.map((c) => c.state), ["baseline", "suspect", "suspect", "suspect", "regressed"]);
});

test("a stopped search claims no speedup — it found nothing", () => {
  const commits: Commit[] = Array.from({ length: 20 }, (_, i) => ({ index: i, sha: `s${i}`, subject: "", author: "", date: "" }));
  const probes: Probe[] = [0, 19].map((index) => ({
    step: index ? 1 : 0, commit: `s${index}`, index, role: index ? "endpoint" : "baseline", raw_scores: [1], median_score: 1,
    pct_change: 0, verdict: index ? "regressed" : "baseline", rounds: 1, nano: [], timestamp: "", wall_s: 0,
  }));
  const s = scannerStats(commits, probes, null, false);
  assert.equal(s.converged, false);
  assert.equal(s.probesUsed, 1);
  // …but the same probes on a search that completed do converge
  assert.equal(scannerStats(commits, probes, null, true).converged, true);
});
