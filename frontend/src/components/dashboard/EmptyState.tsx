// FILE: frontend/src/components/dashboard/EmptyState.tsx — place at this path in the Culprit repo

export function QueuedState() {
  return (
    <div className="flex h-72 flex-col items-center justify-center gap-2 rounded-md border border-dashed border-line-strong text-center">
      <span className="h-2 w-2 animate-pulse rounded-full bg-iris" aria-hidden />
      <p className="text-sm text-ink">Starting up</p>
      <p className="max-w-xs text-xs text-muted">
        Waiting for the first sandbox to spin up. This page updates on its own — no need to refresh.
      </p>
    </div>
  );
}
