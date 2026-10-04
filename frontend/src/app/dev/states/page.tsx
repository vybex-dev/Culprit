// FILE: frontend/src/app/dev/states/page.tsx
//
// Not a hidden debug route — this is the practical answer to BUILD_03's
// "build your first pass against a static fixture file" instruction, kept
// around afterward because it's the fastest way to check every state
// (including the ones "easy to forget," like failed and
// unresolved_diagnosis_only) without needing a live backend or a real
// multi-week-old job. Also useful for recording the demo video.

"use client";

import { useState } from "react";
import { FIXTURES } from "@/fixtures";
import { JobDashboard } from "@/components/dashboard/JobDashboard";
import clsx from "clsx";

export default function StatesPage() {
  // Fixtures are dated days ago, so live ones would show a huge elapsed time.
  // Remember when the fixture was picked and shift the clock so "now" starts
  // at the fixture's own updated_at and ticks from there.
  const [pick, setPick] = useState(() => ({ key: FIXTURES[0].key, at: Date.now() }));
  const selectedKey = pick.key;
  const selected = FIXTURES.find((f) => f.key === selectedKey) ?? FIXTURES[0];
  const clockOffsetMs = Date.parse(selected.job.updated_at) - pick.at;

  return (
    <div className="flex flex-1 flex-col">
      <div className="sticky top-[57px] z-10 border-b border-line bg-paper/95 backdrop-blur">
        {/* One scrolling row on phones (a wrapped, sticky 5-row bar covered ~40% of the
            screen); wraps normally from `sm` up. */}
        <div className="mx-auto flex max-w-7xl items-center gap-1.5 overflow-x-auto px-4 py-3 sm:flex-wrap sm:px-6">
          <span className="mr-1 shrink-0 whitespace-nowrap font-mono text-[11px] text-muted/70">State reference</span>
          {FIXTURES.map((fixture) => (
            <button
              key={fixture.key}
              type="button"
              onClick={() => setPick({ key: fixture.key, at: Date.now() })}
              className={clsx(
                "shrink-0 whitespace-nowrap rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
                fixture.key === selectedKey ? "bg-iris text-paper" : "bg-ink/[0.05] text-muted hover:bg-ink/[0.09]",
              )}
            >
              {fixture.label}
            </button>
          ))}
        </div>
      </div>
      <p className="mx-auto mt-4 max-w-7xl px-6 text-xs text-muted">{selected.description}</p>
      <JobDashboard key={selected.key} job={selected.job} clockOffsetMs={clockOffsetMs} />
    </div>
  );
}
