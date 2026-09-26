// FILE: frontend/src/fixtures/done-resolved.ts — place at this path in the Culprit repo

import {
  buildJob,
  DIAGNOSIS,
  FIX_PATCH_ATTEMPT_1,
  FIX_PATCH_ATTEMPT_2,
  FULL_TIMELINE,
  REGRESSION_COMMIT,
} from "./shared";

export const doneResolvedJob = buildJob({
  job_id: "demo-done-resolved",
  status: "done",
  created_at: "2026-09-20T14:00:00Z",
  updated_at: "2026-09-20T14:44:00Z",
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
    {
      attempt: 2,
      patch: FIX_PATCH_ATTEMPT_2,
      rationale:
        "The first attempt still built a Python-side filter per order after fetching — this groups line items into a dict keyed by order_id up front, so each order does an O(1) dict lookup instead of a re-scan.",
      score_after: 103.4,
      resolved: true,
    },
  ],
  fix: {
    patch_diff: FIX_PATCH_ATTEMPT_2,
    verified: true,
    before_score: 100.9,
    after_score: 103.4,
  },
  final_result: "resolved",
});
