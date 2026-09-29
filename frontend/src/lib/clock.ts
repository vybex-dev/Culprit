// FILE: frontend/src/lib/clock.ts
//
// Live time for the dashboard. The backend only saves job state when a
// stage changes, so anything computed from `updated_at` freezes between
// saves. These helpers instead count against the (skew-corrected) wall
// clock while a job is live, and pin to the recorded end time once it is
// finished — so a timer keeps running during a stage and stops for good
// when the run ends.

"use client";

import { useEffect, useState } from "react";
import type { JobState, JobStatus } from "./types";
import { PIPELINE_STAGES, laneStatus, type PipelineLane } from "./pipeline";

/** Re-renders the caller on an interval while `active`; returns the current
 * time in ms. Idle (no timer at all) when inactive. */
export function useNow(active: boolean, intervalMs = 250): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const tick = () => setNow(Date.now());
    tick();
    const id = setInterval(tick, intervalMs);
    return () => clearInterval(id);
  }, [active, intervalMs]);
  return now;
}

/** ms to add to the browser clock to get server time (server_time − receipt
 * time). 0 when the backend didn't send server_time. */
export function clockOffsetFrom(job: JobState, receivedAtMs: number): number {
  const server = job.server_time ? Date.parse(job.server_time) : NaN;
  return Number.isNaN(server) ? 0 : server - receivedAtMs;
}

const ms = (iso: string | undefined | null): number | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
};

export function isJobLive(job: JobState): boolean {
  return job.status !== "done" && job.status !== "failed";
}

/** When the whole run ended, or null while it's still going. */
export function jobEndMs(job: JobState): number | null {
  if (isJobLive(job)) return null;
  return ms(job.stage_times?.[job.status]) ?? ms(job.updated_at);
}

/** Whole-job elapsed: ticks while live, frozen at the end time when done. */
export function jobElapsedMs(job: JobState, nowMs: number, offsetMs: number): number | null {
  const start = ms(job.created_at);
  if (start === null) return null;
  const end = jobEndMs(job) ?? nowMs + offsetMs;
  return Math.max(0, end - start);
}

const LANE_STAGE: Record<PipelineLane, JobStatus> = {
  bisect: "bisecting",
  diagnose: "diagnosing",
  fix: "fixing",
};

/**
 * How long one lane took (or has been running). Uses the stage_times the
 * backend records; returns null when the lane never ran or the backend
 * predates stage_times — never an estimate.
 */
export function laneElapsedMs(job: JobState, lane: PipelineLane, nowMs: number, offsetMs: number): number | null {
  const times = job.stage_times;
  if (!times || Object.keys(times).length === 0) return null;
  const start = ms(times[LANE_STAGE[lane]]);
  if (start === null) return null;

  const status = laneStatus(job, lane);
  if (status === "idle") return null;
  if (status === "running") return Math.max(0, nowMs + offsetMs - start);

  // Finished (or failed): ends when the next stage began, else when the run did.
  const laterStarts = PIPELINE_STAGES.slice(PIPELINE_STAGES.indexOf(LANE_STAGE[lane] as (typeof PIPELINE_STAGES)[number]) + 1)
    .map((s) => ms(times[s as JobStatus]))
    .filter((t): t is number => t !== null);
  const end = laterStarts.length > 0 ? Math.min(...laterStarts) : (jobEndMs(job) ?? ms(job.updated_at));
  return end === null ? null : Math.max(0, end - start);
}
