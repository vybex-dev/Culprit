// FILE: frontend/src/components/dashboard/RegressionCallout.tsx — place at this path in the Culprit repo

import type { TimelineEntry } from "@/lib/types";
import { formatPctChange, formatScore, shortSha } from "@/lib/format";

function findBaseline(timeline: TimelineEntry[], regressionCommit: string): TimelineEntry | null {
  const index = timeline.findIndex((entry) => entry.commit === regressionCommit);
  if (index <= 0) return null;
  return timeline[index - 1];
}

export function RegressionCallout({
  timeline,
  regressionCommit,
  onOpen,
}: {
  timeline: TimelineEntry[];
  regressionCommit: string;
  onOpen: () => void;
}) {
  const regressed = timeline.find((entry) => entry.commit === regressionCommit);
  const baseline = findBaseline(timeline, regressionCommit);
  if (!regressed) return null;

  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full items-center justify-between gap-3 rounded-md border-l-4 border-butter-700 bg-butter/20 px-4 py-3 text-left transition-colors hover:bg-butter/30"
    >
      <span className="text-sm text-ink">
        Regression found at <span className="font-mono">{shortSha(regressed.commit)}</span>
        {baseline && (
          <>
            {" — "}
            <span className="font-mono">{formatScore(baseline.score)}</span>
            {" → "}
            <span className="font-mono font-medium">{formatScore(regressed.score)}</span>{" "}
            <span className="font-mono text-iris-700">({formatPctChange(baseline.score, regressed.score)})</span>
          </>
        )}
      </span>
      <span className="shrink-0 text-xs font-medium text-iris-700">View diagnosis →</span>
    </button>
  );
}
