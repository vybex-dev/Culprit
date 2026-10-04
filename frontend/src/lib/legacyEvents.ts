// FILE: frontend/src/lib/legacyEvents.ts
//
// Fixtures (the /dev/states reference page) are static JobState snapshots with
// no live event stream behind them. This adapts the old derived trace
// (lib/trace.ts) into LogEvents so the same Terminal can render them. It is
// ONLY for that reference page — real jobs always use the real stream
// (useJobEvents), and the terminal says "recorded" rather than "live" for these.

import type { JobState, LogEvent } from "./types";
import { buildTrace, type TraceKind } from "./trace";

const SOURCE: Record<TraceKind, string> = {
  system: "system",
  sandbox: "sandbox",
  score: "bisect",
  regression: "bisect",
  model: "ultra",
  diagnosis: "ultra",
  patch: "fix",
  verify: "fix",
  done: "system",
  error: "system",
};

export function legacyEvents(job: JobState): LogEvent[] {
  const t0 = Date.parse(job.created_at) || Date.now();
  return buildTrace(job)
    .sort((a, b) => a.seq - b.seq)
    .map((e, i) => ({
      seq: i + 1,
      ts: new Date(t0 + i * 400).toISOString(),
      kind: e.kind === "error" ? "job.failed" : `legacy.${e.kind}`,
      source: SOURCE[e.kind],
      message: e.detail ? `${e.text} — ${e.detail}` : e.text,
      data: {},
    }));
}
