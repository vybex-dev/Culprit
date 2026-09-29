// FILE: frontend/src/fixtures/fixing.ts — place at this path in the Culprit repo

import {
  stageTimes, buildJob,
  DIAGNOSIS,
  FIX_PATCH_ATTEMPT_1,
  FULL_TIMELINE,
  REGRESSION_COMMIT,
} from "./shared";

export const fixingJob = buildJob({
  job_id: "demo-fixing",
  status: "fixing",
  created_at: "2026-09-20T14:00:00Z",
  updated_at: "2026-09-20T14:35:00Z",
  stage_times: stageTimes("2026-09-20T14:00:00Z", "2026-09-20T14:35:00Z", { bisecting: 5, diagnosing: 1570, fixing: 1680 }),
  timeline: FULL_TIMELINE,
  regression_commit: REGRESSION_COMMIT,
  diagnosis: DIAGNOSIS,
  fix_attempts: [
    {
      attempt: 1,
      patch: FIX_PATCH_ATTEMPT_1,
      rationale:
        "Batches the line-item lookup with a single filter(order_id__in=...) call instead of querying per order.",
      score_after: 119.8,
      resolved: false,
    },
  ],
  fix: {
    patch_diff: FIX_PATCH_ATTEMPT_1,
    verified: false,
    before_score: 100.9,
    after_score: 119.8,
  },
});
