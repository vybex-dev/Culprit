// FILE: frontend/src/fixtures/done-regression-undiagnosed.ts — place at this
// path in the Culprit repo
//
// CODE_REVIEW_FINDINGS.md #18/#4: status="done" with a regression_commit
// but no diagnosis. api.py's real pipeline shouldn't reach "done" this way
// today — a diagnose() failure now fails the whole job (status="failed"),
// per api.py's own module docstring flag #2 — but the schema still makes
// diagnosis nullable independently of status, and older/partial job
// records could exist. FinalResultBanner/DrillDownPanel need to render
// this honestly rather than claiming a "confirmed root cause" that was
// never produced; this fixture is what lets that be checked visually
// without waiting for such a job to occur for real.

import { buildJob, FULL_TIMELINE, REGRESSION_COMMIT } from "./shared";

export const doneRegressionUndiagnosedJob = buildJob({
  job_id: "demo-done-regression-undiagnosed",
  status: "done",
  created_at: "2026-09-20T14:00:00Z",
  updated_at: "2026-09-20T14:27:00Z",
  timeline: FULL_TIMELINE.slice(0, 5),
  regression_commit: REGRESSION_COMMIT,
  diagnosis: null,
  fix: null,
  final_result: null,
});
