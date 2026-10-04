"use client";

// FILE: frontend/src/components/dashboard/JobHeader.tsx

import { useState } from "react";
import type { JobState } from "@/lib/types";
import { cancelJob } from "@/lib/api";
import { formatDuration, repoDisplayName, shortSha, truncateMiddle } from "@/lib/format";
import { LiveDot, Pill } from "@/components/ui";
import { CopyMetaTag } from "@/components/CopyMetaTag";
import { isJobLive, jobElapsedMs, useNow } from "@/lib/clock";

const STATUS_VARIANT: Record<JobState["status"], "neutral" | "iris" | "butter" | "resolved" | "unresolved"> = {
  queued: "neutral",
  bisecting: "iris",
  diagnosing: "iris",
  fixing: "iris",
  done: "neutral",
  failed: "unresolved",
  cancelled: "butter",
};

function statusLabel(job: JobState): string {
  if (job.status === "done") {
    return job.final_result === "resolved" ? "Done — resolved" : "Done — diagnosis only";
  }
  return job.status[0].toUpperCase() + job.status.slice(1);
}

/** Total run time. Counts up every second while the job is live and locks
 * to the real end time the moment it finishes. Fixed-width digits so the
 * pill next to it doesn't jitter as it ticks. */
function ElapsedClock({ job, clockOffsetMs }: { job: JobState; clockOffsetMs: number }) {
  const live = isJobLive(job);
  const now = useNow(live, 1000);
  const elapsed = jobElapsedMs(job, now, clockOffsetMs);
  if (elapsed === null) return null;
  return (
    <span
      className={
        // Fixed slot (fits "59m 59s" / "12h 05m"), so the pill/Cancel beside it never shift as the time grows.
        "flex w-[4.5rem] items-center justify-end gap-1.5 whitespace-nowrap font-mono text-xs tabular-nums " +
        (live ? "text-ink" : "text-muted")
      }
      title={live ? "Time since the job started" : "Total run time"}
    >
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden className={live ? "text-butter-700" : "text-muted/70"}>
        <circle cx="8" cy="8.5" r="5.5" stroke="currentColor" strokeWidth="1.4" />
        <path d="M8 5.6V8.6l1.9 1.2M6.5 1.5h3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
      {formatDuration(elapsed)}
    </span>
  );
}

export function JobHeader({
  job,
  isReconnecting,
  clockOffsetMs = 0,
}: {
  job: JobState;
  isReconnecting?: boolean;
  clockOffsetMs?: number;
}) {
  const variant = job.status === "done" && job.final_result === "resolved" ? "resolved" : STATUS_VARIANT[job.status];
  const isLive = isJobLive(job);
  const [cancelState, setCancelState] = useState<"idle" | "sending" | "error">("idle");
  const stopping = job.cancel_requested || cancelState === "sending";

  async function handleCancel() {
    setCancelState("sending");
    try {
      await cancelJob(job.job_id);
      // The poll picks up `cancel_requested` / the final "cancelled" status.
    } catch {
      setCancelState("error");
      setTimeout(() => setCancelState("idle"), 2500);
    }
  }

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <LiveDot variant={variant === "unresolved" ? "unresolved" : "iris"} live={isLive} size="md" />
          <h1 className="truncate text-xl font-medium tracking-tight text-ink">{job.label ?? repoDisplayName(job.repo_url)}</h1>
          {job.mode === "offline" && (
            <span
              className="shrink-0 rounded bg-butter/30 px-1.5 py-0.5 text-[10px] font-medium text-iris-700"
              title="Benchmarks in this run are real measurements. The model reasoning came from Culprit's deterministic offline stand-in, not Nemotron."
            >
              offline stand-in models
            </span>
          )}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <CopyMetaTag copyText={job.benchmark_command} className="max-w-[36ch]">
            {job.benchmark_command}
          </CopyMetaTag>
          {job.commit_range && (
            <CopyMetaTag label="range" copyText={`${job.commit_range[0]}..${job.commit_range[1]}`}>
              {shortSha(job.commit_range[0])}…{shortSha(job.commit_range[1])}
            </CopyMetaTag>
          )}
          <CopyMetaTag label="job" copyText={job.job_id}>
            {truncateMiddle(job.job_id, 20)}
          </CopyMetaTag>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        {isReconnecting && (
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <LiveDot variant="unresolved" live />
            Reconnecting…
          </span>
        )}
        <ElapsedClock job={job} clockOffsetMs={clockOffsetMs} />
        {isLive && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={stopping}
            className="w-[7rem] whitespace-nowrap rounded-md border border-line px-2.5 py-1 text-center text-xs text-muted transition-colors hover:border-unresolved/50 hover:text-unresolved disabled:cursor-default disabled:opacity-60"
            title="Stops at the next checkpoint (between probes, run rounds, or fix attempts) — never mid-benchmark"
          >
            {cancelState === "error" ? "Couldn't cancel" : stopping ? "Stopping…" : "Cancel"}
          </button>
        )}
        {/* Fixed width: "Queued" and "Done — diagnosis only" render in the same box. */}
        <Pill variant={variant} className="w-[10.5rem] justify-center whitespace-nowrap">
          {statusLabel(job)}
        </Pill>
      </div>
    </div>
  );
}
