// FILE: frontend/src/fixtures/done-no-regression.ts — place at this path in the Culprit repo
//
// CODE_REVIEW_FINDINGS.md #18/#4: a completed job that never found a
// regression in the given range — api.py's own module docstring flag #1
// describes this exact case (status="done", regression_commit/diagnosis/
// fix/final_result all honestly null). Before this fixture there was no
// way to see FinalResultBanner's "No regression found" branch in
// /dev/states without a real backend run.

import { stageTimes, buildJob, BENCHMARK_COMMAND, COMMIT_RANGE, REPO_URL } from "./shared";
import type { TimelineEntry } from "@/lib/types";

const T0 = Date.parse("2026-09-21T09:00:00Z");
const minutes = (n: number) => new Date(T0 + n * 60_000).toISOString();

// A flat timeline — every candidate benchmarked within the regression
// threshold of the baseline, so the Bisector never converged on a guilty
// commit at all.
const FLAT_TIMELINE: TimelineEntry[] = [
  { commit: "a1c3e5f7091b2d4a6c8e0f1a3b5c7d9e1f3a5b7c", score: 100.4, timestamp: minutes(0) },
  { commit: "b2d4f6081a2c3e5f7091b2d4a6c8e0f1a3b5c7d9", score: 101.1, timestamp: minutes(9) },
  { commit: "c3e5a7192b3d4f6081a2c3e5f7091b2d4a6c8e0f", score: 99.6, timestamp: minutes(18) },
  { commit: "d4f6b8203c4e5a7192b3d4f6081a2c3e5f7091b2", score: 100.9, timestamp: minutes(27) },
];

export const doneNoRegressionJob = buildJob({
  job_id: "demo-done-no-regression",
  status: "done",
  repo_url: REPO_URL,
  benchmark_command: BENCHMARK_COMMAND,
  commit_range: COMMIT_RANGE,
  created_at: "2026-09-21T09:00:00Z",
  updated_at: "2026-09-21T09:31:00Z",
  stage_times: stageTimes("2026-09-21T09:00:00Z", "2026-09-21T09:31:00Z", { bisecting: 5, done: "end" }),
  timeline: FLAT_TIMELINE,
  regression_commit: null,
  diagnosis: null,
  fix: null,
  final_result: null,
});
