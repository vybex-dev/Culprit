// FILE: frontend/src/app/job/[jobId]/JobPageClient.tsx — place at this path in the Culprit repo

"use client";

import { useJobPolling } from "@/lib/useJobPolling";
import { JobDashboard } from "@/components/dashboard/JobDashboard";

export function JobPageClient({ jobId }: { jobId: string }) {
  const { job, fatalError, isReconnecting } = useJobPolling(jobId);

  if (fatalError) {
    return (
      <div className="mx-auto flex w-full max-w-xl flex-1 flex-col items-center justify-center gap-3 px-6 py-16 text-center">
        <p className="text-sm font-medium text-ink">Couldn&apos;t load this job.</p>
        <p className="max-w-sm text-xs text-muted">{fatalError}</p>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="mx-auto flex w-full max-w-xl flex-1 flex-col items-center justify-center gap-2 px-6 py-16 text-center">
        <span className="h-2 w-2 animate-pulse rounded-full bg-iris" aria-hidden />
        <p className="text-sm text-muted">Loading job…</p>
      </div>
    );
  }

  return <JobDashboard job={job} isReconnecting={isReconnecting} />;
}
