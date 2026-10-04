// FILE: frontend/src/components/dashboard/DashboardSkeleton.tsx
//
// Shown while the first poll is in flight. It mirrors the real dashboard's
// layout (header, ribbon, three lanes, chart + terminal) so the page
// doesn't jump when data lands — a skeleton shaped like the content reads
// as "loading this", where a lone spinner says nothing about what's coming.

import { Skeleton } from "@/components/ui";

export function DashboardSkeleton() {
  return (
    <div
      className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6"
      aria-busy="true"
      aria-live="polite"
    >
      <span className="sr-only">Loading job…</span>

      <div className="space-y-3">
        <Skeleton className="h-7 w-64" />
        <div className="flex flex-wrap gap-1.5">
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-6 w-44" />
          <Skeleton className="h-6 w-36" />
        </div>
      </div>

      <Skeleton className="h-8 w-full" />

      <div className="rounded-2xl border border-line bg-surface-2 p-4">
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="overflow-hidden rounded-xl">
              <Skeleton className="h-28 w-full" />
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <div className="overflow-hidden rounded-xl">
          <Skeleton className="h-[440px] w-full" />
        </div>
        <div className="overflow-hidden rounded-xl">
          <Skeleton className="h-[440px] w-full" />
        </div>
      </div>
    </div>
  );
}
