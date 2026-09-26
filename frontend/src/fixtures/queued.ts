// FILE: frontend/src/fixtures/queued.ts — place at this path in the Culprit repo

import { buildJob } from "./shared";

export const queuedJob = buildJob({
  job_id: "demo-queued",
  status: "queued",
  created_at: "2026-09-20T13:59:40Z",
  updated_at: "2026-09-20T13:59:40Z",
});
