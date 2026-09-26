// FILE: frontend/src/components/dashboard/StatusRibbon.tsx — place at this path in the Culprit repo

import clsx from "clsx";
import type { JobState } from "@/lib/types";
import { PIPELINE_STAGES, STAGE_LABEL, reachedStageIndex } from "@/lib/pipeline";

export function StatusRibbon({ job }: { job: JobState }) {
  const currentIndex = reachedStageIndex(job);
  const failed = job.status === "failed";
  const progressPct = (currentIndex / (PIPELINE_STAGES.length - 1)) * 100;

  return (
    <div className="relative pt-1" aria-label="Pipeline progress" role="group">
      <div className="pointer-events-none absolute left-0 right-0 top-[13px] h-px bg-line" aria-hidden />
      <div
        className={clsx("pointer-events-none absolute left-0 top-[13px] h-px transition-all", failed ? "bg-unresolved/60" : "bg-iris/60")}
        style={{ width: `${progressPct}%` }}
        aria-hidden
      />
      <ol className="relative flex justify-between">
        {PIPELINE_STAGES.map((stage, i) => {
          const isPast = i < currentIndex;
          const isCurrent = i === currentIndex;
          const isFuture = i > currentIndex;
          const isFailedHere = failed && isCurrent;
          const isDoneOk = stage === "done" && isCurrent && job.status === "done";

          return (
            <li key={stage} className="flex flex-col items-center gap-1.5">
              <span
                className={clsx(
                  "h-2.5 w-2.5 rounded-full ring-4 ring-paper transition-colors",
                  isFailedHere && "bg-unresolved",
                  !isFailedHere && (isPast || isDoneOk) && "bg-iris",
                  !isFailedHere && isCurrent && !isDoneOk && "bg-butter-700",
                  !isFailedHere && isFuture && "bg-ink/15",
                )}
              />
              <span
                className={clsx(
                  "text-[11px] whitespace-nowrap",
                  isFuture ? "text-muted/60" : isFailedHere ? "text-unresolved" : "text-muted",
                )}
              >
                {isFailedHere ? "Failed here" : STAGE_LABEL[stage]}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
