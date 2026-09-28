// FILE: frontend/src/components/dashboard/JobHeader.tsx

import type { JobState } from "@/lib/types";
import { formatElapsed, repoDisplayName, shortSha, truncateMiddle } from "@/lib/format";
import { LiveDot, Pill } from "@/components/ui";
import { CopyMetaTag } from "@/components/CopyMetaTag";

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
  const isLive = job.status !== "done" && job.status !== "failed";

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <LiveDot variant={variant === "unresolved" ? "unresolved" : "iris"} live={isLive} size="md" />
          <h1 className="truncate text-xl font-medium tracking-tight text-ink">{repoDisplayName(job.repo_url)}</h1>
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
        <span
          className={
            "text-xs text-muted" + (job.status !== "done" && job.status !== "failed" ? " hidden sm:inline" : "")
          }
        >
          {formatElapsed(job.created_at, job.updated_at)}
        </span>
        <Pill variant={variant}>{statusLabel(job)}</Pill>
      </div>
    </div>
  );
}
