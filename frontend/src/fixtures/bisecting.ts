// FILE: frontend/src/fixtures/bisecting.ts — place at this path in the Culprit repo

import { stageTimes, buildJob, FULL_TIMELINE } from "./shared";

export const bisectingJob = buildJob({
  job_id: "demo-bisecting",
  status: "bisecting",
  created_at: "2026-09-20T14:00:00Z",
  updated_at: "2026-09-20T14:19:00Z",
  stage_times: stageTimes("2026-09-20T14:00:00Z", "2026-09-20T14:19:00Z", { bisecting: 5 }),
  timeline: FULL_TIMELINE.slice(0, 4),
});
