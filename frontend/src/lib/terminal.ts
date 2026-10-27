// FILE: frontend/src/lib/terminal.ts
//
// Turns the backend's raw event stream (backend/events.py) into the rows the
// terminal renders. Pure and deterministic — same events in, same rows out —
// so it is unit-tested (frontend/tests/terminal.test.ts) against a stream
// captured from the real pipeline, not hand-written mocks.
//
// What it does that a naive "one line per event" log can't:
//   * SPANS. `probe.start` … `probe.end` (or a model call, a Tavily search, a
//     fix attempt) become ONE row that shows a live spinner while open and
//     settles into its result when the matching end arrives. Between the two
//     the row is genuinely in-flight — that's a real "running right now".
//   * GROUPING. Individual `sandbox.run` events collapse into a single
//     "benchmark runs" row that fills in run by run.
//   * HONESTY. If the job ends (done / failed / cancelled) with a span still
//     open, that row is marked "interrupted" rather than spinning forever.
//
// Only relative `.ts` imports and `import type`, so Node can run this
// directly under `node --test` with no build step.

import type { LogEvent } from "./types.ts";

export type Tone = "muted" | "text" | "iris" | "accent" | "good" | "bad" | "warn";
export type Glyph = "prompt" | "spin" | "ok" | "fail" | "dot" | "arrow" | "warn" | "star";
export type Group = "system" | "search" | "models" | "sandbox" | "grounding" | "fix";

export interface Chip {
  label: string;
  tone: Tone;
  title?: string;
}

export type Detail =
  | {
      type: "model";
      role: string;
      modelId: string;
      request: unknown;
      response: string | null;
      latencyS: number | null;
      attempts: number | null;
      usage: { prompt_tokens?: number; completion_tokens?: number } | null;
      offline: boolean;
    }
  | { type: "citations"; ok: boolean; lines: { text: string; verified: boolean }[] }
  | {
      type: "tavily";
      query: string;
      refs: { title: string; url: string; score: number | null; snippet: string }[];
      dropped: number;
    }
  | { type: "patch"; patch: string; added: number; removed: number; rationale: string }
  | { type: "text"; text: string }
  | { type: "json"; value: unknown };

export interface RunGroup {
  scores: number[];
  nRuns: number;
  patched: boolean;
  /** true when the backend ran all N inside one VM and reported them together. */
  batched: boolean;
  baseline: number | null;
  thresholdPct: number | null;
}

export interface TermRow {
  id: string;
  seq: number;
  ts: string;
  depth: 0 | 1;
  source: string;
  group: Group;
  glyph: Glyph;
  tone: Tone;
  title: string;
  meta?: string;
  chips?: Chip[];
  tag?: string;
  /** Span still open: the operation is running right now. */
  pending?: boolean;
  /** Span never closed because the job ended first. */
  interrupted?: boolean;
  emphasis?: boolean;
  runs?: RunGroup;
  detail?: Detail;
}

const TERMINAL_KINDS = new Set(["job.done", "job.failed", "job.cancelled"]);

// ---------------------------------------------------------------- helpers

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

export function fmtMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${ms.toFixed(ms < 10 ? 3 : 1)} ms`;
}

export function fmtDur(s: number | null): string {
  if (s === null) return "";
  if (s < 1) return `${Math.round(s * 1000)}ms`;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

function fmtTokens(usage: { prompt_tokens?: number; completion_tokens?: number } | null): string {
  if (!usage) return "";
  const total = (usage.prompt_tokens ?? 0) + (usage.completion_tokens ?? 0);
  if (!total) return "";
  return total >= 1000 ? `${(total / 1000).toFixed(1)}k tok` : `${total} tok`;
}

export function groupOf(source: string): Group {
  switch (source) {
    case "bisect":
    case "git":
      return "search";
    case "nano":
    case "ultra":
      return "models";
    case "sandbox":
      return "sandbox";
    case "tavily":
      return "grounding";
    case "fix":
      return "fix";
    default:
      return "system";
  }
}

/** Two visual levels: pipeline steps at the left edge, what happens *inside* a
 * step (checkouts, runs, model calls, citation checks…) indented beneath. */
function depthOf(kind: string): 0 | 1 {
  if (kind === "sandbox.ready") return 0;
  return /^(sandbox|model|nano|citation|tavily|patch|verify|review|triage)\./.test(kind) || kind === "guardtest.attempt" ? 1 : 0;
}

function base(ev: LogEvent, over: Partial<TermRow>): TermRow {
  return {
    id: `ev:${ev.seq}`,
    seq: ev.seq,
    ts: ev.ts,
    depth: depthOf(ev.kind),
    source: ev.source,
    group: groupOf(ev.source),
    glyph: "dot",
    tone: "muted",
    title: ev.message,
    ...over,
  };
}

// ---------------------------------------------------------- span handling

function describeStart(ev: LogEvent, spanId: string): TermRow {
  const d = ev.data;
  const row = base(ev, { id: `span:${spanId}`, glyph: "spin", tone: "text", pending: true });
  if (ev.kind === "model.start") {
    const role = str(d.role) || ev.source;
    return {
      ...row,
      source: role,
      group: "models",
      tone: "iris",
      tag: d.offline ? "OFFLINE STAND-IN" : undefined,
      detail: {
        type: "model",
        role,
        modelId: str(d.model_id),
        request: d.request ?? null,
        response: null,
        latencyS: null,
        attempts: null,
        usage: null,
        offline: Boolean(d.offline),
      },
    };
  }
  if (ev.kind === "tavily.start") {
    return { ...row, tone: "iris", detail: { type: "tavily", query: str(d.query), refs: [], dropped: 0 } };
  }
  return row;
}

function applyEnd(row: TermRow, ev: LogEvent): TermRow {
  const d = ev.data;
  const dur = num(d.dur_s);
  const out: TermRow = { ...row, pending: false, meta: undefined };

  switch (ev.kind) {
    case "probe.end": {
      const verdict = str(d.verdict);
      if (verdict === "skipped") {
        out.glyph = "warn";
        out.tone = "warn";
        out.chips = [{ label: "skipped", tone: "warn", title: str(d.reason) }];
        out.detail = d.reason ? { type: "text", text: str(d.reason) } : undefined;
        out.meta = dur !== null ? fmtDur(dur) : undefined;
        break;
      }
      const median = num(d.median);
      const pct = num(d.pct_change);
      const bad = verdict === "regressed";
      out.glyph = bad ? "warn" : "ok";
      out.tone = bad ? "bad" : "good";
      out.chips = [{ label: verdict, tone: bad ? "bad" : "good" }];
      const parts: string[] = [];
      if (median !== null) parts.push(fmtMs(median));
      if (pct !== null && verdict !== "baseline") parts.push(`${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`);
      if (dur !== null) parts.push(fmtDur(dur));
      out.meta = parts.join(" · ");
      break;
    }
    case "model.end": {
      const failed = d.ok === false;
      out.glyph = failed ? "fail" : "ok";
      out.tone = failed ? "bad" : "iris";
      const usage = (d.usage as { prompt_tokens?: number; completion_tokens?: number } | undefined) ?? null;
      const latency = num(d.latency_s) ?? dur;
      out.meta = [latency !== null ? fmtDur(latency) : "", fmtTokens(usage)].filter(Boolean).join(" · ");
      if (failed && d.error) out.chips = [{ label: "failed", tone: "bad", title: str(d.error) }];
      if (num(d.attempts) && (num(d.attempts) as number) > 1) {
        out.chips = [...(out.chips ?? []), { label: `${d.attempts} attempts`, tone: "warn", title: "the first reply wasn't valid JSON" }];
      }
      if (out.detail?.type === "model") {
        out.detail = {
          ...out.detail,
          response: typeof d.response === "string" ? d.response : null,
          latencyS: latency,
          attempts: num(d.attempts),
          usage,
        };
      }
      break;
    }
    case "tavily.end": {
      const n = num(d.n_refs) ?? 0;
      out.glyph = d.ok === false ? "fail" : n > 0 ? "ok" : "dot";
      out.tone = d.ok === false ? "bad" : n > 0 ? "iris" : "muted";
      out.meta = [`${n} source${n === 1 ? "" : "s"}`, dur !== null ? fmtDur(dur) : ""].filter(Boolean).join(" · ");
      out.detail = {
        type: "tavily",
        query: str(d.query),
        refs: Array.isArray(d.refs)
          ? (d.refs as Record<string, unknown>[]).map((r) => ({
              title: str(r.title),
              url: str(r.url),
              score: num(r.score),
              snippet: str(r.snippet),
            }))
          : [],
        dropped: num(d.dropped) ?? 0,
      };
      break;
    }
    case "profile.end": {
      const skipped = d.ok === false;
      out.glyph = skipped ? "dot" : "ok";
      out.tone = skipped ? "muted" : "iris";
      out.chips = [
        skipped
          ? { label: "skipped", tone: "muted", title: str(d.reason) }
          : { label: "measured", tone: "iris", title: "cProfile call counts and cumulative times, before vs after the guilty commit" },
      ];
      out.meta = dur !== null ? fmtDur(dur) : undefined;
      break;
    }
    case "fixround.end": {
      const verified = num(d.verified) ?? 0;
      const of = num(d.of);
      const ok = verified > 0;
      out.glyph = ok ? "ok" : "warn";
      out.tone = ok ? "good" : "warn";
      out.chips =
        of !== null
          ? [{ label: ok ? `${verified}/${of} verified` : `0/${of} verified`, tone: ok ? "good" : "warn" }]
          : undefined;
      out.meta = dur !== null ? fmtDur(dur) : undefined;
      break;
    }
    case "guardtest.end": {
      const verified = d.verified === true;
      out.glyph = verified ? "ok" : "warn";
      out.tone = verified ? "good" : "warn";
      out.chips = [
        verified
          ? { label: "proven red → green", tone: "good", title: "Failed on the regressed commit, passed with the fix — in real sandbox runs." }
          : { label: "not shipped", tone: "warn", title: str(d.reason) || "No test could be proven, so none is offered." },
      ];
      if (verified && d.passes_on_good === false) {
        out.chips.push({ label: "not green on last good commit", tone: "warn", title: "May be tied to the fix's exact shape." });
      }
      out.meta = dur !== null ? fmtDur(dur) : undefined;
      break;
    }
    case "tests.end": {
      const guard = str(d.guard);
      const active = guard === "active";
      out.glyph = active ? "ok" : "warn";
      out.tone = active ? "good" : "warn";
      out.chips = [
        {
          label: active ? "gates every fix" : guard === "baseline_failing" ? "already failing" : "unavailable",
          tone: active ? "good" : "warn",
          title: ev.message,
        },
      ];
      if (d.output && !active) out.detail = { type: "text", text: str(d.output) };
      out.meta = dur !== null ? fmtDur(dur) : undefined;
      break;
    }
    case "fix.end": {
      const resolved = d.resolved === true;
      const crashed = typeof d.error === "string" && d.error.length > 0;
      const testsRejected = d.tests_passed === false;
      out.glyph = resolved ? "ok" : "fail";
      out.tone = resolved ? "good" : "bad";
      out.chips = [
        crashed
          ? { label: "crashed — no score", tone: "bad", title: str(d.error) }
          : testsRejected
            ? { label: "fast, but tests failed", tone: "bad", title: ev.message }
            : { label: resolved ? "verified" : "not resolved", tone: resolved ? "good" : "bad" },
      ];
      // A crashed attempt never produced a measurement: don't show its placeholder score.
      const score = crashed ? null : num(d.score_after);
      out.meta = [score !== null ? fmtMs(score) : "", dur !== null ? fmtDur(dur) : ""].filter(Boolean).join(" · ");
      break;
    }
    case "repo.end":
    case "sandbox.instance.end": {
      const ok = d.ok !== false;
      out.glyph = ok ? "ok" : "fail";
      out.tone = ok ? "good" : "bad";
      out.title = ev.message;
      out.meta = dur !== null ? fmtDur(dur) : undefined;
      break;
    }
    default:
      out.glyph = "ok";
      out.tone = "muted";
      out.meta = dur !== null ? fmtDur(dur) : undefined;
  }
  return out;
}

// ------------------------------------------------------------ point events

function describePoint(ev: LogEvent): TermRow {
  const d = ev.data;
  switch (ev.kind) {
    case "job.start":
      return base(ev, {
        glyph: "prompt",
        tone: "text",
        title: `culprit analyze ${str(d.repo)} --bench "${str(d.benchmark)}" --threshold ${d.threshold_pct}% --runs ${d.n_runs}`,
        chips: [
          { label: str(d.mode), tone: d.mode === "offline" ? "warn" : "iris" },
          { label: str(d.sandbox) === "token_factory" ? "token factory sandboxes" : "local sandbox", tone: "muted" },
        ],
        detail: { type: "json", value: d },
      });
    case "stage.start":
      return base(ev, { glyph: "star", tone: "accent", emphasis: true });
    case "job.waiting":
      return base(ev, { glyph: "spin", tone: "warn", pending: true });
    case "git.call":
      return base(ev, {
        tone: "muted",
        meta: num(d.dur_s) !== null ? fmtDur(num(d.dur_s)) : undefined,
        glyph: d.ok === false ? "fail" : "dot",
      });
    case "git.range":
      return base(ev, { glyph: "arrow", tone: "iris", chips: d.auto ? [{ label: "auto-detected", tone: "iris" }] : undefined });
    case "sandbox.ready":
      return base(ev, { glyph: "dot", tone: "iris" });
    case "bisect.plan":
      return base(ev, {
        glyph: "arrow",
        tone: "iris",
        chips: [
          { label: `≤ ${d.max_probes} probes`, tone: "iris" },
          { label: `vs ${d.linear_runs} linear`, tone: "muted" },
          ...((num(d.parallelism) ?? 1) > 1
            ? [{ label: `${num(d.parallelism)} at a time · ~${num(d.rounds) ?? "?"} rounds`, tone: "iris" as const }]
            : []),
        ],
      });
    case "bisect.round": {
      const n = Array.isArray(d.commits) ? d.commits.length : num(d.parallelism);
      return base(ev, {
        glyph: "arrow",
        tone: "iris",
        chips: n ? [{ label: `${n} in parallel`, tone: "iris" }] : undefined,
      });
    }
    case "bisect.window": {
      const lo = num(d.lo);
      const hi = num(d.hi);
      const size = lo !== null && hi !== null ? Math.max(0, hi - lo + 1) : null;
      return base(ev, {
        glyph: "arrow",
        tone: "text",
        chips: size !== null ? [{ label: size === 0 ? "converged" : `${size} left`, tone: size === 0 ? "good" : "muted" }] : undefined,
      });
    }
    case "bisect.skip":
      return base(ev, { glyph: "warn", tone: "warn", chips: [{ label: "skipped", tone: "warn" }] });
    case "bisect.done": {
      const par = num(d.parallelism) ?? 1;
      const probeT = num(d.probe_time_s);
      const wallT = num(d.wall_s);
      const chips: Chip[] =
        par > 1 && probeT !== null && wallT !== null && wallT > 0 && probeT > wallT * 1.05
          ? [{ label: `${(probeT / wallT).toFixed(1)}× faster than one at a time`, tone: "good" }]
          : [];
      return base(ev, {
        glyph: d.regression_commit ? "warn" : "ok",
        tone: d.regression_commit ? "accent" : "good",
        emphasis: true,
        chips: chips.length ? chips : undefined,
      });
    }
    case "sandbox.checkout":
      return base(ev, { meta: num(d.dur_s) !== null ? fmtDur(num(d.dur_s)) : undefined });
    case "sandbox.deps":
      return base(ev, {
        chips: [{ label: d.cache_hit ? "cache hit" : "cache miss", tone: d.cache_hit ? "good" : "warn" }],
        meta: num(d.dur_s) !== null ? fmtDur(num(d.dur_s)) : undefined,
      });
    case "sandbox.patch":
      return base(ev, { glyph: "ok", tone: "iris" });
    case "nano.verdict": {
      const v = str(d.final_verdict);
      const chips: Chip[] = [{ label: v, tone: v === "regressed" ? "bad" : v === "clean" ? "good" : "warn" }];
      if (d.overridden) {
        chips.push({
          label: "arithmetic override",
          tone: "warn",
          title: `Nano said "${str(d.nano_verdict)}" but median-vs-threshold arithmetic says "${v}" — the arithmetic wins and the discrepancy is logged.`,
        });
      }
      return base(ev, {
        glyph: d.overridden ? "warn" : "arrow",
        tone: d.overridden ? "warn" : "iris",
        chips,
        detail: { type: "json", value: d },
      });
    }
    case "model.retry":
      return base(ev, { glyph: "warn", tone: "warn", detail: { type: "text", text: str(d.raw) } });
    case "diagnose.start":
      return base(ev, { glyph: "arrow", tone: "iris" });
    case "citation.check": {
      const ok = d.ok === true;
      const lines = Array.isArray(d.lines) ? (d.lines as { text: string; verified: boolean }[]) : [];
      return base(ev, {
        glyph: ok ? "ok" : "fail",
        tone: ok ? "good" : "bad",
        detail: lines.length ? { type: "citations", ok, lines } : undefined,
      });
    }
    case "citation.retry":
    case "citation.pruned":
    case "citation.downgrade":
      return base(ev, { glyph: "warn", tone: "warn" });
    case "tavily.skipped":
      return base(ev, { glyph: "dot", tone: "muted", chips: [{ label: "skipped", tone: "muted" }] });
    case "diagnose.result":
      return base(ev, {
        glyph: "ok",
        tone: "accent",
        emphasis: true,
        depth: 0,
        chips: [
          { label: str(d.category).replace(/_/g, " "), tone: "accent" },
          { label: `${str(d.confidence)} confidence`, tone: "muted" },
        ],
      });
    case "patch.built":
      return base(ev, {
        glyph: "arrow",
        tone: "iris",
        chips: [
          { label: `+${d.added}`, tone: "good" },
          { label: `−${d.removed}`, tone: "bad" },
        ],
        detail: { type: "patch", patch: str(d.patch), added: num(d.added) ?? 0, removed: num(d.removed) ?? 0, rationale: str(d.rationale) },
      });
    case "sandbox.tests":
      return base(ev, { glyph: d.passed ? "ok" : "fail", tone: d.passed ? "good" : "bad" });
    case "fix.selected":
      return base(ev, {
        glyph: "ok",
        tone: "good",
        emphasis: true,
        chips: [{ label: `kept candidate ${num(d.candidate) ?? "?"}`, tone: "good", title: "The best-measured candidate among those that verified." }],
      });
    case "fix.candidate_failed":
      return base(ev, { glyph: "warn", tone: "warn", detail: d.error ? { type: "text", text: str(d.error) } : undefined });
    case "guardtest.attempt": {
      const status = str(d.status);
      const ok = status === "verified";
      return base(ev, {
        glyph: ok ? "ok" : "warn",
        tone: ok ? "good" : "warn",
        chips: [{ label: status.replace(/_/g, " "), tone: ok ? "good" : "warn" }],
        detail: !ok && d.note ? { type: "text", text: str(d.note) } : undefined,
      });
    }
    case "tests.result": {
      const passed = d.passed === true;
      return base(ev, {
        glyph: passed ? "ok" : "fail",
        tone: passed ? "good" : "bad",
        emphasis: !passed,
        chips: [{ label: passed ? "tests pass" : "tests fail", tone: passed ? "good" : "bad" }],
        detail: d.output ? { type: "text", text: str(d.output) } : undefined,
      });
    }
    case "triage.result":
      return base(ev, {
        glyph: "ok",
        tone: "iris",
        chips: [{ label: `${num(d.files) ?? "?"} files ranked`, tone: "iris", title: "Nano reordered the files; nothing was hidden or changed." }],
      });
    case "triage.unavailable":
      return base(ev, { glyph: "warn", tone: "muted" });
    case "review.result": {
      const flagged = d.verdict === "suspicious";
      const concerns = Array.isArray(d.concerns) ? d.concerns.map((c) => str(c)) : [];
      return base(ev, {
        glyph: flagged ? "warn" : "ok",
        tone: flagged ? "warn" : "good",
        chips: [{ label: flagged ? "flagged" : "no red flags", tone: flagged ? "warn" : "good", title: "Advisory only — never decides verified." }],
        detail: flagged && concerns.length ? { type: "text", text: concerns.join("\n") } : undefined,
      });
    }
    case "review.rule":
      return base(ev, {
        glyph: "warn",
        tone: "warn",
        chips: [{ label: "flagged", tone: "warn", title: "Deterministic rule — advisory only." }],
        detail: Array.isArray(d.concerns) && d.concerns.length ? { type: "text", text: d.concerns.map((c) => str(c)).join("\n") } : undefined,
      });
    case "review.unavailable":
      return base(ev, { glyph: "warn", tone: "muted" });
    case "patch.rejected":
    case "verify.crashed":
      return base(ev, { glyph: "fail", tone: "bad", detail: d.error ? { type: "text", text: str(d.error) } : undefined });
    case "verify.result": {
      const resolved = d.resolved === true;
      return base(ev, { glyph: resolved ? "ok" : "warn", tone: resolved ? "good" : "warn", emphasis: resolved });
    }
    case "job.done":
      return base(ev, { glyph: "ok", tone: d.outcome === "resolved" || d.outcome === "no_regression" ? "good" : "accent", emphasis: true });
    case "job.failed":
      return base(ev, { glyph: "fail", tone: "bad", emphasis: true, detail: d.error ? { type: "text", text: str(d.error) } : undefined });
    case "job.cancel_requested":
    case "job.cancelled":
      return base(ev, { glyph: "warn", tone: "warn", emphasis: ev.kind === "job.cancelled" });
    default:
      return base(ev, {});
  }
}

// ------------------------------------------------------------------ build

export function buildRows(events: LogEvent[]): TermRow[] {
  const rows: TermRow[] = [];
  const index = new Map<string, number>();
  let baseline: number | null = null;
  let thresholdPct: number | null = null;
  let terminal = false;

  const push = (row: TermRow) => {
    index.set(row.id, rows.length);
    rows.push(row);
  };

  for (const ev of events) {
    const d = ev.data;
    if (ev.kind === "bisect.plan") thresholdPct = num(d.threshold_pct);
    if (ev.kind === "probe.end" && d.role === "baseline") baseline = num(d.median);
    if (TERMINAL_KINDS.has(ev.kind)) terminal = true;

    const span = typeof d.span === "string" && d.span ? d.span : null;

    if (span && ev.kind.endsWith(".start")) {
      push(describeStart(ev, span));
      continue;
    }
    if (span && ev.kind.endsWith(".end")) {
      const id = `span:${span}`;
      const at = index.get(id);
      if (at !== undefined) {
        rows[at] = applyEnd(rows[at], ev);
      } else {
        // An end with no start (client joined mid-stream): still show its result.
        push(applyEnd(describeStart({ ...ev, kind: ev.kind.replace(/\.end$/, ".start") }, span), ev));
      }
      continue;
    }

    if (ev.kind === "sandbox.run") {
      const nRuns = num(d.n_runs) ?? 0;
      const key = `runs:${d.step ?? "v"}:${d.attempt ?? 0}:${d.patched ? 1 : 0}:${str(d.commit)}`;
      const score = num(d.score);
      const at = index.get(key);
      if (at !== undefined && score !== null) {
        const r = rows[at];
        const runs = { ...(r.runs as RunGroup), scores: [...(r.runs as RunGroup).scores, score] };
        rows[at] = { ...r, runs, pending: runs.scores.length < runs.nRuns };
      } else if (score !== null) {
        const patched = Boolean(d.patched);
        const batched = Boolean(d.batched);
        push({
          id: key,
          seq: ev.seq,
          ts: ev.ts,
          depth: 1,
          source: "sandbox",
          group: "sandbox",
          glyph: "dot",
          tone: patched ? "iris" : "text",
          title: patched ? "benchmark runs on the patched code" : "benchmark runs",
          pending: nRuns > 1,
          tag: batched ? "batched from one VM" : undefined,
          runs: { scores: [score], nRuns, patched, batched, baseline, thresholdPct },
        });
        // A batched backend reports every run together: nothing is "in flight".
        if (nRuns <= 1) rows[rows.length - 1].pending = false;
      }
      continue;
    }

    push(describePoint(ev));
  }

  // Runs rows carry baseline/threshold *as known when created*; refresh them
  // with the final values so early rows (baseline probe) color correctly.
  for (const r of rows) {
    if (r.runs) r.runs = { ...r.runs, baseline: r.runs.baseline ?? baseline, thresholdPct: r.runs.thresholdPct ?? thresholdPct };
  }

  if (terminal) {
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (r.pending) {
        rows[i] = { ...r, pending: false, interrupted: true, glyph: "fail", tone: "muted", meta: r.meta ?? "interrupted" };
      }
    }
  }
  return rows;
}

// ---------------------------------------------------------------- filters

export type TermFilter = "all" | Group;

export const FILTER_LABELS: { value: TermFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "search", label: "Search" },
  { value: "models", label: "Models" },
  { value: "sandbox", label: "Sandbox" },
  { value: "grounding", label: "Grounding" },
  { value: "fix", label: "Fix" },
];

export function filterRows(rows: TermRow[], filter: TermFilter): TermRow[] {
  if (filter === "all") return rows;
  // System rows (stage headers, job start/finish) always stay: they're the
  // landmarks that make a filtered view still read as a story.
  return rows.filter((r) => r.group === filter || r.group === "system");
}

export function countByGroup(rows: TermRow[]): Record<Group, number> {
  const out: Record<Group, number> = { system: 0, search: 0, models: 0, sandbox: 0, grounding: 0, fix: 0 };
  for (const r of rows) out[r.group] += 1;
  return out;
}

// ----------------------------------------------------------------- export

/** Plain-text transcript (what "Copy log" puts on the clipboard). */
export function eventsToText(events: LogEvent[]): string {
  const t0 = events.length ? Date.parse(events[0].ts) : 0;
  return events
    .map((e) => {
      const t = ((Date.parse(e.ts) - t0) / 1000).toFixed(2).padStart(7, " ");
      return `+${t}s  [${e.source.padEnd(7, " ")}]  ${e.message}`;
    })
    .join("\n");
}

/** Newline-delimited JSON — the full-fidelity log, requests and responses included. */
export function eventsToJsonl(events: LogEvent[]): string {
  return events.map((e) => JSON.stringify(e)).join("\n") + "\n";
}
