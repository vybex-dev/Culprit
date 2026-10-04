// FILE: frontend/src/app/job/[jobId]/JobPageClient.tsx

"use client";

import { useJobPolling } from "@/lib/useJobPolling";
import { useJobEvents } from "@/lib/useJobEvents";
import { JobDashboard } from "@/components/dashboard/JobDashboard";
import { DashboardSkeleton } from "@/components/dashboard/DashboardSkeleton";
import { LiveDot } from "@/components/ui";
import { ActivitySignal } from "@/components/ActivitySignal";

export function JobPageClient({ jobId }: { jobId: string }) {
  const { job, fatalError, isReconnecting, clockOffsetMs } = useJobPolling(jobId);
  const { events } = useJobEvents(jobId);

  if (fatalError) {
    return (
      <div className="mx-auto flex w-full max-w-xl flex-1 flex-col items-center justify-center gap-3 px-6 py-16 text-center">
        <LiveDot variant="unresolved" />
        <p className="text-sm font-medium text-ink">Couldn&apos;t load this job.</p>
        <p className="max-w-sm text-xs text-muted">{fatalError}</p>
      </div>
    );
  }

  if (!job)
    return (
      <>
        <ActivitySignal />
        <DashboardSkeleton />
      </>
    );

  return <JobDashboard job={job} events={events} isReconnecting={isReconnecting} clockOffsetMs={clockOffsetMs} />;
}
