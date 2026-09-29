// FILE: frontend/src/fixtures/failed.ts — place at this path in the Culprit repo

import { stageTimes, buildJob, FULL_TIMELINE } from "./shared";

export const failedJob = buildJob({
  job_id: "demo-failed",
  status: "failed",
  created_at: "2026-09-20T14:00:00Z",
  updated_at: "2026-09-20T14:09:00Z",
  stage_times: stageTimes("2026-09-20T14:00:00Z", "2026-09-20T14:09:00Z", { bisecting: 5, failed: "end" }),
  timeline: FULL_TIMELINE.slice(0, 2),
  error:
    "SandboxError: dependency install failed for commit 3b8d4c1f (pip install exited 1 — pinned numpy==1.24.0 has no wheel for this Python version). Bisection stopped rather than guessing a score for this commit.",
});
