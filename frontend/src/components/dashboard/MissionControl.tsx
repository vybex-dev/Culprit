// FILE: frontend/src/components/dashboard/MissionControl.tsx
//
// The "watch it work" centerpiece: three lanes (Bisect / Diagnose / Fix)
// shown side by side, each a self-contained readout of what that stage
// of the pipeline actually did — in the spirit of an agent-orchestration
// IDE's Manager view (parallel workstreams, each with its own status and
// artifacts) rather than a single progress bar. The pipeline here is
// genuinely sequential, not parallel, so this doesn't pretend otherwise:
// only one lane is ever "running" at a time, the others sit at idle/done/
// failed — but all three stay visible throughout, which is the point.
//
// Every summary line is derived from real JobState fields (lib/pipeline's
// laneStatus + this file's laneSummary) — nothing here is a fabricated
// progress percentage or an invented timer (AGENTS.md rule 1).

"use client";

import { useMemo } from "react";
import clsx from "clsx";
import { motion, useReducedMotion } from "motion/react";
import type { JobState } from "@/lib/types";
import {
  LANE_LABEL,
  PIPELINE_LANES,
  PIPELINE_STAGES,
  reachedStageIndex,
  laneStatus,
  type LaneStatus,
  type PipelineLane,
} from "@/lib/pipeline";
import { buildTrace, type TraceEvent } from "@/lib/trace";
import { CONFIDENCE_LABEL, categoryLabel, formatScore, shortSha } from "@/lib/format";
import { LiveDot } from "@/components/ui";
import { AgentCursor, type CursorWaypoint } from "./AgentCursor";

const LANE_X: Record<PipelineLane, number> = { bisect: 16.6, diagnose: 50, fix: 83.4 };
const CURSOR_Y = 58;

export function BisectIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
      <circle cx="5" cy="4" r="1.8" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="5" cy="12" r="1.8" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="12" cy="8" r="1.8" stroke="currentColor" strokeWidth="1.3" />
      <path d="M5 5.8V10.2M6.6 8H10.4M6.7 4.9l3.7 2.2M6.7 11.1l3.7-2.2" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}
export function DiagnoseIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
      <circle cx="6.5" cy="6.5" r="4" stroke="currentColor" strokeWidth="1.3" />
      <path d="M9.4 9.4 13 13" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}
export function FixIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M9.9 3.2a2.6 2.6 0 0 0-3.4 3.1L2 10.8l1.9 1.9 4.5-4.5a2.6 2.6 0 0 0 3.1-3.4l-1.8 1.8-1.4-1.4 1.6-1.7Z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
    </svg>
  );
}
const LANE_ICON: Record<PipelineLane, () => React.ReactElement> = {
  bisect: BisectIcon,
  diagnose: DiagnoseIcon,
  fix: FixIcon,
};

type BadgeTone = "running" | "ok" | "warn" | "muted" | "failed";

/**
 * What to *say* about a lane. laneStatus() only knows whether the
 * pipeline moved past a stage ("done"), not whether that stage produced
 * anything — and this project's whole premise (AGENTS.md rule 1, and
 * FinalResultBanner's own variants) is that green must never imply a
 * result that doesn't exist. So a lane that finished without its
 * artifact — no diagnosis attached, or a fix that never verified — is
 * badged honestly instead of a blanket green "Done".
 */
function laneBadge(job: JobState, lane: PipelineLane, status: LaneStatus): { text: string; tone: BadgeTone } {
  if (status === "running") return { text: "Running", tone: "running" };
  if (status === "failed") return { text: "Failed", tone: "failed" };
  if (status === "idle") return { text: "Not started", tone: "muted" };

  if (lane === "diagnose" && !job.diagnosis) {
    return job.regression_commit ? { text: "No diagnosis", tone: "warn" } : { text: "Skipped", tone: "muted" };
  }
  if (lane === "fix" && !job.fix?.verified) {
    return job.fix_attempts.length === 0 ? { text: "Skipped", tone: "muted" } : { text: "Not fixed", tone: "warn" };
  }
  return { text: "Done", tone: "ok" };
}

const BADGE_TEXT_CLASS: Record<BadgeTone, string> = {
  running: "text-butter-700",
  ok: "text-resolved",
  warn: "text-butter-700",
  muted: "text-muted/70",
  failed: "text-unresolved",
};

const BADGE_DOT: Record<BadgeTone, "butter" | "resolved" | "unresolved" | "neutral"> = {
  running: "butter",
  ok: "resolved",
  warn: "butter",
  muted: "neutral",
  failed: "unresolved",
};

function laneSummary(job: JobState, lane: PipelineLane, status: LaneStatus): string | null {
  if (status === "idle") return null;

  if (lane === "bisect") {
    const n = job.timeline.length;
    if (n === 0) return status === "failed" ? (job.error ?? "Sandbox failed before scoring a commit.") : "Starting first sandbox…";
    const parts = [`${n} commit${n === 1 ? "" : "s"} scored`];
    if (job.regression_commit) parts.push(`regression at ${shortSha(job.regression_commit)}`);
    else if (status === "done") parts.push("no regression in range");
    return parts.join(" — ");
  }

  if (lane === "diagnose") {
    if (job.diagnosis) return `${categoryLabel(job.diagnosis.category)} — ${CONFIDENCE_LABEL[job.diagnosis.confidence]}`;
    if (status === "running") return "Reading the guilty diff…";
    if (status === "failed") return job.error ?? "Stopped before finishing.";
    if (status === "done" && !job.regression_commit) return "Nothing to diagnose.";
    if (status === "done") return "No diagnosis was attached to this job.";
    return null;
  }

  // fix
  if (job.fix_attempts.length === 0) {
    if (status === "running") return "Generating a patch from the diagnosis…";
    if (status === "failed") return job.error ?? "Stopped before finishing.";
    if (status === "done") return "No fix was attempted.";
    return null;
  }
  const lastAttempt = job.fix_attempts[job.fix_attempts.length - 1];
  if (job.fix?.verified) {
    return `Verified — ${formatScore(job.fix.before_score)} → ${formatScore(job.fix.after_score)}`;
  }
  return `Attempt ${lastAttempt.attempt} — ${lastAttempt.resolved ? "resolved" : "still regressed"} (${formatScore(lastAttempt.score_after)})`;
}

function cursorWaypointFor(job: JobState, trace: TraceEvent[]): CursorWaypoint | null {
  const last = trace[trace.length - 1];

  if (job.status === "queued") {
    return { id: "queued", xPct: LANE_X.bisect, yPct: CURSOR_Y, label: last?.text ?? "waiting on a sandbox…" };
  }
  const activeLane: PipelineLane | null =
    job.status === "bisecting" ? "bisect" : job.status === "diagnosing" ? "diagnose" : job.status === "fixing" ? "fix" : null;

  if (activeLane) {
    return { id: last?.id ?? activeLane, xPct: LANE_X[activeLane], yPct: CURSOR_Y, label: last?.text ?? LANE_LABEL[activeLane] };
  }

  // Terminal state — park wherever the pipeline last actually was.
  const reached = reachedStageIndex(job);
  const parkedLane: PipelineLane =
    reached >= PIPELINE_STAGES.indexOf("fixing") ? "fix" : reached >= PIPELINE_STAGES.indexOf("diagnosing") ? "diagnose" : "bisect";
  return { id: `parked-${last?.id ?? parkedLane}`, xPct: LANE_X[parkedLane], yPct: CURSOR_Y, label: last?.text ?? "done" };
}

function LaneCard({ job, lane }: { job: JobState; lane: PipelineLane }) {
  const status = laneStatus(job, lane);
  const summary = laneSummary(job, lane, status);
  const badge = laneBadge(job, lane, status);
  const Icon = LANE_ICON[lane];
  const reduceMotion = useReducedMotion();
  const opacity = status === "idle" ? 0.55 : badge.tone === "muted" ? 0.7 : 1;

  return (
    <motion.div
      className={clsx(
        "relative flex flex-col gap-2.5 rounded-xl border p-3.5 transition-colors sm:pb-8",
        status === "running" && "border-transparent bg-surface glow-butter",
        status === "done" && "border-line bg-surface",
        status === "failed" && "border-transparent bg-surface glow-unresolved",
        status === "idle" && "border-line/70 bg-surface/60",
      )}
      animate={{ opacity }}
      transition={{ duration: 0.3 }}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-ink">
          <Icon />
          <span className="text-[13px] font-medium">{LANE_LABEL[lane]}</span>
        </div>
        <span
          className={clsx(
            "flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide",
            BADGE_TEXT_CLASS[badge.tone],
          )}
        >
          <LiveDot variant={BADGE_DOT[badge.tone]} live={badge.tone === "running"} />
          {badge.text}
        </span>
      </div>

      <div className="min-h-[2.5rem] text-[12.5px] leading-snug text-muted">
        {summary ?? <span className="text-muted/60">Waiting for the previous stage.</span>}
      </div>

      {status === "running" && !reduceMotion && (
        <motion.div
          className="absolute inset-x-3.5 bottom-0 h-px overflow-hidden"
          aria-hidden
        >
          <motion.div
            className="h-full w-1/3 bg-butter"
            animate={{ x: ["-100%", "340%"] }}
            transition={{ duration: 1.8, repeat: Infinity, ease: "linear" }}
          />
        </motion.div>
      )}
    </motion.div>
  );
}

export function MissionControl({ job }: { job: JobState }) {
  const trace = useMemo(() => buildTrace(job), [job]);
  const waypoint = useMemo(() => cursorWaypointFor(job, trace), [job, trace]);
  const isLive = job.status !== "done" && job.status !== "failed";

  return (
    <div className="relative overflow-hidden rounded-2xl border border-line bg-surface-2">
      <div className="mc-grid pointer-events-none absolute inset-0 opacity-70" aria-hidden />
      {isLive && (
        <div
          className="aurora-drift pointer-events-none absolute -inset-24 opacity-40"
          style={{
            background:
              "radial-gradient(38% 55% at 20% 30%, color-mix(in srgb, var(--iris) 30%, transparent), transparent 70%), radial-gradient(32% 45% at 80% 65%, color-mix(in srgb, var(--butter) 24%, transparent), transparent 70%)",
          }}
          aria-hidden
        />
      )}

      <div className="relative flex items-center justify-between border-b border-line px-4 py-2.5">
        <div className="flex items-center gap-2">
          <LiveDot variant="iris" live={isLive} size="md" />
          <span className="font-mono text-[11px] tracking-wide text-muted">Mission control</span>
        </div>
        <span className="font-mono text-[11px] text-muted/70">3 stages · sequential</span>
      </div>

      <div className="relative grid grid-cols-1 gap-3 p-4 sm:grid-cols-3">
        {PIPELINE_LANES.map((lane) => (
          <LaneCard key={lane} job={job} lane={lane} />
        ))}
        {/* Lanes only sit side by side from `sm` up — stacked, the cursor's
            percentage coordinates would point at the wrong card (it'd say
            "fix verified" hovering over Bisect), so it's hidden there. The
            lane badges and summaries carry the same information. */}
        <div className="pointer-events-none absolute inset-0 hidden sm:block">
          <AgentCursor waypoint={waypoint} />
        </div>
      </div>
    </div>
  );
}
