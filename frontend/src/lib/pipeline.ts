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
