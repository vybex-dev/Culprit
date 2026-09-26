// FILE: frontend/src/components/dashboard/ErrorState.tsx — place at this path in the Culprit repo

export function ErrorBanner({ error }: { error: string | null }) {
  return (
    <div className="rounded-md border-l-4 border-unresolved bg-unresolved/[0.06] px-4 py-3">
      <p className="text-sm font-medium text-ink">This job failed.</p>
      <p className="mt-1 font-mono text-xs leading-relaxed text-ink/80">
        {error ?? "No error detail was recorded for this job."}
      </p>
    </div>
  );
}
