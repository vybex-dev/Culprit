// FILE: frontend/src/lib/trace.ts — place at this path in the Culprit repo
//
// Turns a JobState (or a sequence of JobStates observed over time) into a
// timestamped log of discrete events: "sandbox started on commit X",
// "commit X scored", "regression located", "diagnosis received", etc.
//
// This is the data source for the live trace feed (TraceFeed.tsx) — the
// thing that makes the dashboard feel like it's watching an agent work,
// the way Antigravity/Stitch surface a visible activity log rather than a
// silent spinner. Every event here is derived from a real field on
// JobState; nothing is invented. Two jobs with identical state produce
// identical traces. If the backend hasn't given us a piece of data yet,
// there is no event for it yet — we don't simulate progress that hasn't
// happened.

import type { Diagnosis, Fix, FixAttempt, JobState, TimelineEntry } from "./types";
import { categoryLabel, formatPctChange, formatScore, shortSha } from "./format";

export type TraceKind =
  | "system"
  | "sandbox"
  | "score"
  | "regression"
  | "model"
  | "diagnosis"
  | "patch"
  | "verify"
  | "done"
  | "error";

export interface TraceEvent {
  id: string;
  kind: TraceKind;
  text: string;
  detail?: string;
  /** Sort/dedupe key — stable across re-renders of the same job state. */
  seq: number;
}

function scoreLine(entry: TimelineEntry, index: number, prev: TimelineEntry | undefined): TraceEvent {
  const delta = prev ? entry.score - prev.score : 0;
  const pct = prev ? formatPctChange(prev.score, entry.score) : null;
  const jumped = prev && entry.score > prev.score * 1.12;
  return {
    id: `score-${entry.commit}`,
    seq: 100 + index * 10,
    kind: jumped ? "regression" : "score",
    text: `commit ${shortSha(entry.commit)} scored ${formatScore(entry.score)}`,
    detail: pct && delta !== 0 ? (jumped ? `${pct} — regression candidate` : pct) : undefined,
  };
}

function diagnosisLines(diagnosis: Diagnosis): TraceEvent[] {
  const events: TraceEvent[] = [
    {
      id: "model-diagnoser",
      seq: 5000,
      kind: "model",
      text: "Nemotron 3 Ultra: analyzing guilty diff",
      detail: "full file context, not just the hunk",
    },
    {
      id: "diagnosis-category",
      seq: 5010,
      kind: "diagnosis",
      text: `root cause classified — ${categoryLabel(diagnosis.category)}`,
      detail: `${diagnosis.confidence} confidence`,
    },
  ];
  if (diagnosis.tavily_refs.length > 0) {
    events.push({
      id: "diagnosis-tavily",
      seq: 5020,
      kind: "diagnosis",
      text: `grounded against ${diagnosis.tavily_refs.length} external reference${diagnosis.tavily_refs.length > 1 ? "s" : ""} via Tavily`,
    });
  }
  return events;
}

function attemptLines(attempt: FixAttempt): TraceEvent[] {
  return [
    {
      id: `patch-${attempt.attempt}`,
      seq: 6000 + attempt.attempt * 10,
      kind: "patch",
      text: `patch attempt ${attempt.attempt} generated`,
      detail: attempt.rationale,
    },
    {
      id: `verify-${attempt.attempt}`,
      seq: 6000 + attempt.attempt * 10 + 5,
      kind: "verify",
      text: `sandbox re-run: ${formatScore(attempt.score_after)}`,
      detail: attempt.resolved ? "within threshold — resolved" : "still above baseline threshold",
    },
  ];
}

function finalLines(job: JobState, fix: Fix | null): TraceEvent[] {
  if (job.status === "failed") {
    return [
      {
        id: "final-failed",
        seq: 9000,
        kind: "error",
        text: "job failed",
        detail: job.error ?? undefined,
      },
    ];
  }
  if (job.status !== "done") return [];
  if (job.final_result === "resolved" && fix) {
    return [
      {
        id: "final-resolved",
        seq: 9000,
        kind: "done",
        text: "fix verified in sandbox",
        detail: `${formatScore(fix.before_score)} → ${formatScore(fix.after_score)}`,
      },
    ];
  }
  return [
    {
      id: "final-unresolved",
      seq: 9000,
      kind: "done",
      text: "diagnosis complete — no verified fix",
      detail: "attempt cap reached; root cause reported as-is",
    },
  ];
}

/** Builds the full ordered trace for a job's current state. Pure function
 * of `job` — safe to call on every render/poll tick. */
export function buildTrace(job: JobState): TraceEvent[] {
  const events: TraceEvent[] = [
    {
      id: "system-start",
      seq: 0,
      kind: "system",
      text: `job created for ${job.repo_url.replace(/^https?:\/\//, "")}`,
    },
  ];

  if (job.status === "queued") {
    events.push({
      id: "system-waiting",
      seq: 10,
      kind: "sandbox",
      text: "waiting for first Token Factory Sandbox to spin up",
    });
    return events;
  }

  events.push({
    id: "system-bisect-start",
    seq: 20,
    kind: "sandbox",
    text: "bisector started — binary search over commit history",
    detail: job.commit_range
      ? `${shortSha(job.commit_range[0])}…${shortSha(job.commit_range[1])}`
      : "full history",
  });

  job.timeline.forEach((entry, i) => {
    events.push(scoreLine(entry, i, job.timeline[i - 1]));
  });

  if (job.regression_commit) {
    events.push({
      id: "regression-located",
      seq: 4000,
      kind: "regression",
      text: `guilty commit located — ${shortSha(job.regression_commit)}`,
    });
  }

  if (job.diagnosis) {
    events.push(...diagnosisLines(job.diagnosis));
  } else if (job.status === "diagnosing") {
    events.push({
      id: "model-diagnoser-pending",
      seq: 4500,
      kind: "model",
      text: "Nemotron 3 Ultra: analyzing guilty diff",
    });
  }

  for (const attempt of job.fix_attempts) {
    events.push(...attemptLines(attempt));
  }
  if (job.status === "fixing" && job.fix_attempts.length === 0) {
    events.push({
      id: "patch-pending",
      seq: 5900,
      kind: "patch",
      text: "generating patch from diagnosis",
    });
  }

  events.push(...finalLines(job, job.fix));

  return events.sort((a, b) => a.seq - b.seq);
}
