// FILE: frontend/src/components/dashboard/ErrorState.tsx

export function ErrorBanner({ error }: { error: string | null }) {
  return (
    <div className="glow-unresolved flex items-start gap-2.5 rounded-xl border border-transparent bg-unresolved/[0.08] px-4 py-3">
      <span className="mt-0.5 text-unresolved" aria-hidden>
        ✕
      </span>
      <div>
        <p className="text-sm font-medium text-ink">This job failed.</p>
        <p className="mt-1 font-mono text-xs leading-relaxed text-ink/80">
          {error ?? "No error detail was recorded for this job."}
        </p>
      </div>
    </div>
  );
}
