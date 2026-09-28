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
  const [selectedKey, setSelectedKey] = useState(FIXTURES[0].key);
  const selected = FIXTURES.find((f) => f.key === selectedKey) ?? FIXTURES[0];

  return (
    <div className="flex flex-1 flex-col">
      <div className="sticky top-[57px] z-10 border-b border-line bg-paper/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-1.5 px-6 py-3">
          <span className="mr-1 font-mono text-[11px] text-muted/70">State reference</span>
          {FIXTURES.map((fixture) => (
            <button
              key={fixture.key}
              type="button"
              onClick={() => setSelectedKey(fixture.key)}
              className={clsx(
                "rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
                fixture.key === selectedKey ? "bg-iris text-paper" : "bg-ink/[0.05] text-muted hover:bg-ink/[0.09]",
              )}
            >
              {fixture.label}
            </button>
          ))}
        </div>
      </div>
      <p className="mx-auto mt-4 max-w-6xl px-6 text-xs text-muted">{selected.description}</p>
      <JobDashboard key={selected.key} job={selected.job} />
    </div>
  );
}
