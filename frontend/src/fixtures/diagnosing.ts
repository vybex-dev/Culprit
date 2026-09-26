// FILE: frontend/src/fixtures/diagnosing.ts — place at this path in the Culprit repo

import { buildJob, FULL_TIMELINE, REGRESSION_COMMIT } from "./shared";

export const diagnosingJob = buildJob({
  job_id: "demo-diagnosing",
  status: "diagnosing",
  created_at: "2026-09-20T14:00:00Z",
  updated_at: "2026-09-20T14:27:30Z",
  timeline: FULL_TIMELINE,
  regression_commit: REGRESSION_COMMIT,
});
