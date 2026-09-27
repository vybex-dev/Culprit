// FILE: frontend/src/fixtures/index.ts — place at this path in the Culprit repo

import type { JobState } from "@/lib/types";
import { bisectingJob } from "./bisecting";
import { bisectingRegressionFoundJob } from "./bisecting-regression-found";
import { diagnosingJob } from "./diagnosing";
import { doneNoRegressionJob } from "./done-no-regression";
import { doneRegressionUndiagnosedJob } from "./done-regression-undiagnosed";
import { doneResolvedJob } from "./done-resolved";
import { doneUnresolvedJob } from "./done-unresolved";
import { failedJob } from "./failed";
import { fixingJob } from "./fixing";
import { queuedJob } from "./queued";

export interface FixtureEntry {
  key: string;
  label: string;
  description: string;
  job: JobState;
}

export const FIXTURES: FixtureEntry[] = [
  { key: "queued", label: "Queued", description: "Job created, no sandbox run yet.", job: queuedJob },
  { key: "bisecting", label: "Bisecting", description: "Chart filling in, no regression located yet.", job: bisectingJob },
  {
    key: "regression-found",
    label: "Regression found",
    description: 'regression_commit is set while status is still "bisecting".',
    job: bisectingRegressionFoundJob,
  },
  { key: "diagnosing", label: "Diagnosing", description: "Regression point is clickable; diagnosis is still loading.", job: diagnosingJob },
  { key: "fixing", label: "Fixing", description: "Diagnosis done; one failed fix attempt logged, not hidden.", job: fixingJob },
  { key: "done-resolved", label: "Done — resolved", description: "Fix verified in a sandbox re-run.", job: doneResolvedJob },
  {
    key: "done-unresolved",
    label: "Done — unresolved",
    description: "Attempt cap hit; honest diagnosis-only outcome.",
    job: doneUnresolvedJob,
  },
  {
    key: "done-no-regression",
    label: "Done — no regression",
    description: "Every commit in range benchmarked clean; nothing to diagnose.",
    job: doneNoRegressionJob,
  },
  {
    key: "done-regression-undiagnosed",
    label: "Done — undiagnosed",
    description: "Regression found but no diagnosis attached — a defensive state, not an expected outcome.",
    job: doneRegressionUndiagnosedJob,
  },
  { key: "failed", label: "Failed", description: "Job errored out mid-run.", job: failedJob },
];
