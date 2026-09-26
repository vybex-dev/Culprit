// FILE: frontend/src/components/dashboard/JobHeader.tsx — place at this path in the Culprit repo

import clsx from "clsx";
import type { JobState } from "@/lib/types";
import { formatElapsed, repoDisplayName, shortSha, truncateMiddle } from "@/lib/format";
import { Pill } from "@/components/ui";

const STATUS_VARIANT: Record<JobState["status"], "neutral" | "iris" | "butter" | "resolved" | "unresolved"> = {
  queued: "neutral",
  bisecting: "iris",
  diagnosing: "iris",
  fixing: "iris",
  done: "neutral",
  failed: "unresolved",
};

function statusLabel(job: JobState): string {
  if (job.status === "done") {
    return job.final_result === "resolved" ? "Done — resolved" : "Done — diagnosis only";
  }
  return job.status[0].toUpperCase() + job.status.slice(1);
}

export function JobHeader({
  job,
  isReconnecting,
}: {
  job: JobState;
  isReconnecting?: boolean;
}) {
  const variant = job.status === "done" && job.final_result === "resolved" ? "resolved" : STATUS_VARIANT[job.status];

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h1 className="truncate text-xl font-medium tracking-tight text-ink">
          {repoDisplayName(job.repo_url)}
        </h1>
        <dl className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-muted">
          <div className="flex items-center gap-1">
            <dt className="sr-only">Benchmark command</dt>
            <dd className="max-w-[36ch] truncate" title={job.benchmark_command}>
              {job.benchmark_command}
            </dd>
          </div>
          {job.commit_range && (
            <>
              <span aria-hidden>·</span>
              <div>
                <dt className="sr-only">Commit range</dt>
                <dd>
                  {shortSha(job.commit_range[0])}…{shortSha(job.commit_range[1])}
                </dd>
              </div>
            </>
          )}
          <span aria-hidden>·</span>
          <div>
            <dt className="sr-only">Job ID</dt>
            <dd title={job.job_id}>{truncateMiddle(job.job_id, 24)}</dd>
          </div>
        </dl>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        {isReconnecting && (
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-unresolved" aria-hidden />
            Reconnecting…
          </span>
        )}
        <span
          className={clsx(
            "text-xs text-muted",
            job.status !== "done" && job.status !== "failed" && "hidden sm:inline",
          )}
        >
          {formatElapsed(job.created_at, job.updated_at)}
        </span>
        <Pill variant={variant}>{statusLabel(job)}</Pill>
      </div>
    </div>
  );
}
