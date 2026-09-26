// FILE: frontend/src/fixtures/bisecting-regression-found.ts — place at this path in the Culprit repo
//
// Per BUILD_00_OVERVIEW.md: "the dashboard's 'found regression' moment is
// derived by the frontend, not a separate status: it's regression_commit
// != null while status is still bisecting/diagnosing." This fixture is
// that exact window — the bisector has converged but the job hasn't
// advanced to "diagnosing" yet.

import { buildJob, FULL_TIMELINE, REGRESSION_COMMIT } from "./shared";

export const bisectingRegressionFoundJob = buildJob({
  job_id: "demo-regression-found",
  status: "bisecting",
  created_at: "2026-09-20T14:00:00Z",
  updated_at: "2026-09-20T14:26:00Z",
  timeline: FULL_TIMELINE.slice(0, 5),
  regression_commit: REGRESSION_COMMIT,
});
