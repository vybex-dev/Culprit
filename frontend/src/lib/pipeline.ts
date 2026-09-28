// FILE: frontend/src/lib/pipeline.ts — place at this path in the Culprit repo

import type { JobState } from "./types";

export const PIPELINE_STAGES = ["queued", "bisecting", "diagnosing", "fixing", "done"] as const;
export type PipelineStage = (typeof PIPELINE_STAGES)[number];

export const STAGE_LABEL: Record<PipelineStage, string> = {
  queued: "Queued",
  bisecting: "Bisecting",
  diagnosing: "Diagnosing",
  fixing: "Fixing",
  done: "Done",
};

/**
 * `status: "failed"` isn't one of the five pipeline stages, so there's
 * nothing to look up directly. Instead this infers the furthest stage the
 * job actually reached from what data is present, so the ribbon can show
 * "got this far, then failed" instead of collapsing to a blank state.
 * This is an inference for display only — it never invents a result the
 * job didn't actually produce (AGENTS.md rule 1).
 */
export function reachedStageIndex(job: JobState): number {
  if (job.status !== "failed") {
    return PIPELINE_STAGES.indexOf(job.status as PipelineStage);
  }
  if (job.fix_attempts.length > 0 || job.fix) return PIPELINE_STAGES.indexOf("fixing");
  if (job.diagnosis) return PIPELINE_STAGES.indexOf("diagnosing");
  if (job.regression_commit) return PIPELINE_STAGES.indexOf("diagnosing");
  if (job.timeline.length > 0) return PIPELINE_STAGES.indexOf("bisecting");
  return PIPELINE_STAGES.indexOf("queued");
}

/**
 * The three worker lanes shown side by side in Mission Control. These
 * are a subset of PIPELINE_STAGES (queued/done aren't "workers") — kept
 * as a separate type rather than reusing PipelineStage so a lane can
 * never accidentally be asked for "queued" or "done" status.
 */
export const PIPELINE_LANES = ["bisect", "diagnose", "fix"] as const;
export type PipelineLane = (typeof PIPELINE_LANES)[number];

export const LANE_LABEL: Record<PipelineLane, string> = {
  bisect: "Bisect",
  diagnose: "Diagnose",
  fix: "Fix",
};

export type LaneStatus = "idle" | "running" | "done" | "failed";

const LANE_STAGE_INDEX: Record<PipelineLane, number> = {
  bisect: PIPELINE_STAGES.indexOf("bisecting"),
  diagnose: PIPELINE_STAGES.indexOf("diagnosing"),
  fix: PIPELINE_STAGES.indexOf("fixing"),
};

/**
 * Status of one lane, derived purely from real JobState fields — same
 * "infer the furthest real stage, never invent one" discipline as
 * reachedStageIndex above (AGENTS.md rule 1: never fabricate what
 * happened). A lane is never marked "running" or "done" from anything
 * but the job's own recorded progress.
 */
export function laneStatus(job: JobState, lane: PipelineLane): LaneStatus {
  const reached = reachedStageIndex(job);
  const laneIndex = LANE_STAGE_INDEX[lane];

  if (job.status === "failed") {
    if (reached === laneIndex) return "failed";
    if (reached < laneIndex) {
      // A failure recorded before any lane-specific data exists (e.g. the
      // first sandbox never came up) has nowhere else to attach — it
      // belongs to the first lane, not to three lanes that all look idle.
      return reached === 0 && lane === "bisect" ? "failed" : "idle";
    }
    return "done";
  }

  if (reached < laneIndex) return "idle";
  if (reached > laneIndex) return "done";
  return job.status === "done" ? "done" : "running";
}
