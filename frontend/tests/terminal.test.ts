// Unit tests for the terminal's event → row logic. Run: `npm test`.
// The main fixtures are a REAL stream captured from the pipeline (see
// tests/fixtures/), so these tests fail if the backend's output shape drifts
// from what the UI expects.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { buildRows, countByGroup, eventsToJsonl, eventsToText, filterRows } from "../src/lib/terminal.ts";
import type { LogEvent } from "../src/lib/types.ts";

const load = <T>(name: string): T => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), "utf8"));
const events = load<LogEvent[]>("demo-events.json");
const job = load<{ probes: unknown[]; metrics: { models: Record<string, { calls: number }> } }>("demo-job.json");

let seq = 0;
const ev = (kind: string, source: string, message: string, data: Record<string, unknown> = {}): LogEvent => ({
  seq: ++seq, ts: new Date(1_800_000_000_000 + seq * 100).toISOString(), kind, source, message, data,
});

// ------------------------------------------------------------- real stream

test("a real finished run has no spinners and nothing interrupted", () => {
  const rows = buildRows(events);
  assert.ok(rows.length > 40);
  assert.equal(rows.filter((r) => r.pending).length, 0);
  assert.equal(rows.filter((r) => r.interrupted).length, 0);
});

test("it opens with the command that was run and closes with the outcome", () => {
  const rows = buildRows(events);
  assert.equal(rows[0].glyph, "prompt");
  assert.match(rows[0].title, /^culprit analyze .*--bench "python bench\.py" --threshold 15% --runs 5$/);
  const last = rows[rows.length - 1];
  assert.equal(last.tone, "good");
  assert.match(last.title, /fix verified/);
});

test("each probe is exactly one row, settled with its verdict", () => {
  const rows = buildRows(events).filter((r) => r.id.startsWith("span:") && /^probe \d/.test(r.title));
  assert.equal(rows.length, job.probes.length);
  assert.ok(rows.every((r) => r.chips?.length === 1 && r.meta));
  assert.equal(rows.filter((r) => r.chips?.[0].label === "regressed").length >= 2, true);
  assert.ok(rows.some((r) => r.chips?.[0].label === "baseline"));
});

test("individual runs collapse into one row per probe, with every raw score", () => {
  const runs = buildRows(events).filter((r) => r.runs && !r.runs.patched);
  assert.equal(runs.length, job.probes.length);
  assert.ok(runs.every((r) => r.runs!.scores.length === 5 && r.runs!.nRuns === 5));
  const patched = buildRows(events).filter((r) => r.runs?.patched);
  assert.equal(patched.length, 1);
  assert.equal(patched[0].runs!.scores.length, 5);
  // threshold + baseline are known for every runs row (needed to color the bars)
  assert.ok(runs.every((r) => r.runs!.baseline !== null && r.runs!.thresholdPct === 15));
});

test("model calls carry the exact request, raw response and stand-in label", () => {
  const models = buildRows(events).filter((r) => r.detail?.type === "model");
  assert.equal(models.length, job.metrics.models.nano.calls + job.metrics.models.ultra.calls);
  for (const m of models) {
    const d = m.detail;
    assert.ok(d && d.type === "model" && d.response !== null);
    assert.equal(m.tag, "OFFLINE STAND-IN");
    assert.equal(m.depth, 1);
  }
});

test("citation check is shown with per-line verification", () => {
  const cite = buildRows(events).find((r) => r.detail?.type === "citations");
  assert.ok(cite && cite.detail?.type === "citations");
  assert.equal(cite.glyph, "ok");
  assert.ok(cite.detail.lines.every((l) => l.verified));
});

test("depth: pipeline steps at the edge, sandbox internals nested", () => {
  const rows = buildRows(events);
  assert.ok(rows.filter((r) => r.title.startsWith("probe ")).every((r) => r.depth === 0));
  assert.ok(rows.filter((r) => r.runs).every((r) => r.depth === 1));
  assert.ok(rows.filter((r) => r.title.startsWith("dependencies")).every((r) => r.depth === 1));
});

test("filters keep the system landmarks so a filtered view still reads as a story", () => {
  const rows = buildRows(events);
  const models = filterRows(rows, "models");
  assert.ok(models.length < rows.length);
  assert.ok(models.some((r) => r.group === "system"));
  assert.ok(models.filter((r) => r.group !== "system").every((r) => r.group === "models"));
  assert.equal(filterRows(rows, "all").length, rows.length);
  const counts = countByGroup(rows);
  assert.equal(Object.values(counts).reduce((a, b) => a + b, 0), rows.length);
});

test("exports: text transcript and lossless JSONL", () => {
  const text = eventsToText(events);
  assert.equal(text.split("\n").length, events.length);
  assert.match(text.split("\n")[0], /^\+\s+0\.00s\s+\[system \]/);
  const jsonl = eventsToJsonl(events).trim().split("\n").map((l) => JSON.parse(l));
  assert.deepEqual(jsonl, events);
});

// -------------------------------------------------------- live / edge cases

test("an open span is a live spinner; its end settles the SAME row in place", () => {
  seq = 0;
  const start = ev("probe.start", "bisect", "probe 2 · midpoint abc12345", { span: "s1", step: 2 });
  const live = buildRows([start]);
  assert.equal(live.length, 1);
  assert.equal(live[0].pending, true);
  assert.equal(live[0].glyph, "spin");

  const end = ev("probe.end", "bisect", "clean · median 9.8 ms", { span: "s1", dur_s: 1.8, verdict: "clean", median: 9.8, pct_change: -0.3 });
  const done = buildRows([start, end]);
  assert.equal(done.length, 1);
  assert.equal(done[0].id, "span:s1");
  assert.equal(done[0].pending, false);
  assert.equal(done[0].title, "probe 2 · midpoint abc12345"); // header kept, result moves to chips/meta
  assert.equal(done[0].chips![0].label, "clean");
  assert.match(done[0].meta!, /9\.800 ms · -0\.3% · 1\.8s/);
});

test("runs fill in one by one and only stop 'running' when all N have landed", () => {
  seq = 0;
  const run = (i: number, score: number) => ev("sandbox.run", "sandbox", `run ${i}/3`, { step: 1, commit: "c", run: i, n_runs: 3, score, patched: false });
  assert.equal(buildRows([run(1, 10)])[0].pending, true);
  const two = buildRows([run(1, 10), run(2, 11)]);
  assert.equal(two.length, 1);
  assert.deepEqual(two[0].runs!.scores, [10, 11]);
  assert.equal(two[0].pending, true);
  assert.equal(buildRows([run(1, 10), run(2, 11), run(3, 9)])[0].pending, false);
});

test("patched and unpatched runs of the same commit never merge", () => {
  seq = 0;
  const mk = (patched: boolean) => ev("sandbox.run", "sandbox", "run", { commit: "c", run: 1, n_runs: 1, score: 5, patched });
  assert.equal(buildRows([mk(false), mk(true)]).length, 2);
});

test("batched (single-VM) runs are labelled and never look in-flight", () => {
  seq = 0;
  const r = buildRows([ev("sandbox.run", "sandbox", "run", { commit: "c", run: 1, n_runs: 1, score: 5, batched: true })]);
  assert.equal(r[0].pending, false);
  assert.equal(r[0].tag, "batched from one VM");
});

test("a job that ends with a span still open marks it interrupted, never spinning forever", () => {
  seq = 0;
  const rows = buildRows([
    ev("probe.start", "bisect", "probe 3", { span: "s9" }),
    ev("sandbox.run", "sandbox", "run", { step: 3, commit: "c", run: 1, n_runs: 5, score: 9 }),
    ev("job.failed", "system", "SandboxError: boom", { error: "SandboxError: boom" }),
  ]);
  assert.equal(rows.filter((r) => r.pending).length, 0);
  assert.equal(rows.filter((r) => r.interrupted).length, 2);
  assert.equal(rows[rows.length - 1].glyph, "fail");
});

test("an end with no start (client joined mid-stream) still shows its result", () => {
  seq = 0;
  const rows = buildRows([ev("tavily.end", "tavily", "2 sources", { span: "t1", n_refs: 2, dur_s: 0.4, query: "q", refs: [] })]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].pending, false);
  assert.match(rows[0].meta!, /2 sources/);
});

test("a crashed fix attempt shows no score — its number was never measured", () => {
  seq = 0;
  const start = ev("fix.start", "fix", "fix attempt 1/3", { span: "f1" });
  const crashed = buildRows([start, ev("fix.end", "fix", "x", { span: "f1", score_after: 297.9, resolved: false, error: "RuntimeError: boom", dur_s: 2 })]);
  assert.equal(crashed[0].chips![0].label, "crashed — no score");
  assert.doesNotMatch(crashed[0].meta ?? "", /297/);
  const ok = buildRows([start, ev("fix.end", "fix", "x", { span: "f1", score_after: 9.7, resolved: true, dur_s: 2 })]);
  assert.equal(ok[0].chips![0].label, "verified");
  assert.match(ok[0].meta!, /9\.700 ms/);
});

test("an arithmetic override of the small model is flagged, not hidden", () => {
  seq = 0;
  const r = buildRows([ev("nano.verdict", "nano", "Nano said…", { nano_verdict: "regressed", final_verdict: "clean", overridden: true })]);
  assert.equal(r[0].glyph, "warn");
  assert.ok(r[0].chips!.some((c) => c.label === "arithmetic override" && /arithmetic wins/.test(c.title!)));
});

// ------------------------------------------------------------- parallel probing

test("a parallel plan, round and finish are labelled; a sequential run shows none of it", () => {
  seq = 0;
  const plan = buildRows([ev("bisect.plan", "bisect", "12 commits", { max_probes: 7, linear_runs: 12, parallelism: 3, rounds: 3 })]);
  assert.ok(plan[0].chips!.some((c) => /3 at a time/.test(c.label) && /3 rounds/.test(c.label)));
  const seqPlan = buildRows([ev("bisect.plan", "bisect", "12 commits", { max_probes: 4, linear_runs: 12, parallelism: 1, rounds: 4 })]);
  assert.ok(!seqPlan[0].chips!.some((c) => /at a time/.test(c.label)));

  const round = buildRows([ev("bisect.round", "bisect", "round 1: probing 3 commit(s) at once", { round: 1, commits: ["a", "b", "c"] })]);
  assert.ok(round[0].chips!.some((c) => c.label === "3 in parallel"));

  const done = buildRows([ev("bisect.done", "bisect", "found", { regression_commit: "abc", parallelism: 3, probe_time_s: 30, wall_s: 12 })]);
  assert.ok(done[0].chips!.some((c) => /2\.5× faster/.test(c.label)));
  // no speed-up claim unless the measured numbers show one
  const noClaim = buildRows([ev("bisect.done", "bisect", "found", { regression_commit: "abc", parallelism: 3, probe_time_s: 10, wall_s: 10 })]);
  assert.equal(noClaim[0].chips, undefined);
  const plain = buildRows([ev("bisect.done", "bisect", "found", { regression_commit: "abc", probes: 4, linear_runs: 12 })]);
  assert.equal(plain[0].chips, undefined);
});

// ------------------------------------------------------------- regression guard test

test("the guard-test stage reads as proven only when the sandbox proved it", () => {
  seq = 0;
  const start = ev("guardtest.start", "fix", "regression guard", { span: "g1" });
  const proven = buildRows([start, ev("guardtest.end", "fix", "verified", { span: "g1", verified: true, passes_on_good: true, dur_s: 9 })]);
  assert.equal(proven[0].glyph, "ok");
  assert.ok(proven[0].chips!.some((c) => c.label === "proven red → green" && c.tone === "good"));
  assert.ok(!proven[0].chips!.some((c) => /last good/.test(c.label)));

  const brittle = buildRows([start, ev("guardtest.end", "fix", "verified", { span: "g1", verified: true, passes_on_good: false, dur_s: 9 })]);
  assert.ok(brittle[0].chips!.some((c) => /not green on last good commit/.test(c.label)));

  const none = buildRows([start, ev("guardtest.end", "fix", "none", { span: "g1", verified: false, reason: "passed on the regressed code", dur_s: 9 })]);
  assert.equal(none[0].glyph, "warn");
  assert.ok(none[0].chips!.some((c) => c.label === "not shipped" && /passed on the regressed code/.test(c.title!)));
  assert.ok(!none[0].chips!.some((c) => /proven/.test(c.label)));
});

test("a guard-test attempt row carries its status and, when rejected, why", () => {
  seq = 0;
  const bad = buildRows([ev("guardtest.attempt", "fix", "attempt 1: rejected", { attempt: 1, status: "did_not_fail", note: "it passed on the regression" })]);
  assert.equal(bad[0].glyph, "warn");
  assert.equal(bad[0].depth, 1);
  assert.equal(bad[0].chips![0].label, "did not fail");
  assert.deepEqual(bad[0].detail, { type: "text", text: "it passed on the regression" });
  const ok = buildRows([ev("guardtest.attempt", "fix", "attempt 1: verified", { attempt: 1, status: "verified", note: "pins the count" })]);
  assert.equal(ok[0].glyph, "ok");
  assert.equal(ok[0].detail, undefined);
});

// ------------------------------------------------------------- best-of-N fixes

test("a fix round shows how many candidates verified, and which one was kept", () => {
  seq = 0;
  const start = ev("fixround.start", "fix", "fix round 1/3", { span: "r1", round: 1, candidates: 3 });
  const won = buildRows([start, ev("fixround.end", "fix", "round 1", { span: "r1", verified: 2, of: 3, dur_s: 12 })]);
  assert.equal(won[0].glyph, "ok");
  assert.ok(won[0].chips!.some((c) => c.label === "2/3 verified" && c.tone === "good"));
  const lost = buildRows([start, ev("fixround.end", "fix", "round 1", { span: "r1", verified: 0, of: 3, dur_s: 12 })]);
  assert.equal(lost[0].glyph, "warn");
  assert.ok(lost[0].chips!.some((c) => c.label === "0/3 verified" && c.tone === "warn"));

  const sel = buildRows([ev("fix.selected", "fix", "picked candidate 2", { candidate: 2, verified: 2, of: 3 })]);
  assert.ok(sel[0].chips!.some((c) => c.label === "kept candidate 2"));
  const failed = buildRows([ev("fix.candidate_failed", "fix", "candidate 3 could not be produced", { candidate: 3, error: "schema-invalid" })]);
  assert.equal(failed[0].glyph, "warn");
  assert.deepEqual(failed[0].detail, { type: "text", text: "schema-invalid" });
});
