// FILE: frontend/src/components/dashboard/StatusRibbon.tsx
//
// The slim five-point overview (queued → bisecting → diagnosing → fixing
// → done) — the "how far along is the whole job" read, complementing
// Mission Control's richer per-lane detail below it.

import clsx from "clsx";
import { motion, useReducedMotion } from "motion/react";
import type { JobState } from "@/lib/types";
import { PIPELINE_STAGES, STAGE_LABEL, reachedStageIndex } from "@/lib/pipeline";

function StageMarker({
  isPast,
  isCurrent,
  isFuture,
  isFailedHere,
  isDoneOk,
  isRunning,
}: {
  isPast: boolean;
  isCurrent: boolean;
  isFuture: boolean;
  isFailedHere: boolean;
  isDoneOk: boolean;
  isRunning: boolean;
}) {
  return (
    <span className="relative flex h-2.5 w-2.5 items-center justify-center">
      {isRunning && <span className="breathe-ring absolute inset-0 rounded-full bg-butter-700" aria-hidden />}
      <span
        className={clsx(
          "relative h-2.5 w-2.5 rounded-full ring-4 ring-paper transition-colors",
          isFailedHere && "bg-unresolved",
          !isFailedHere && (isPast || isDoneOk) && "bg-iris",
          !isFailedHere && isCurrent && !isDoneOk && "bg-butter-700",
          !isFailedHere && isFuture && "bg-ink/15",
        )}
      />
    </span>
  );
}

export function StatusRibbon({ job }: { job: JobState }) {
  const currentIndex = reachedStageIndex(job);
  const failed = job.status === "failed";
  const progressPct = (currentIndex / (PIPELINE_STAGES.length - 1)) * 100;
  const reduceMotion = useReducedMotion();

  return (
    <div className="relative pt-1" aria-label="Pipeline progress" role="group">
      <div className="pointer-events-none absolute left-0 right-0 top-[13px] h-px bg-line" aria-hidden />
      <motion.div
        className={clsx("pointer-events-none absolute left-0 top-[13px] h-px", failed ? "bg-unresolved/60" : "bg-iris/60")}
        initial={false}
        animate={{ width: `${progressPct}%` }}
        transition={{ duration: reduceMotion ? 0 : 0.45, ease: "easeOut" }}
        aria-hidden
      />
      <ol className="relative flex justify-between">
        {PIPELINE_STAGES.map((stage, i) => {
          const isPast = i < currentIndex;
          const isCurrent = i === currentIndex;
          const isFuture = i > currentIndex;
          const isFailedHere = failed && isCurrent;
          const isDoneOk = stage === "done" && isCurrent && job.status === "done";
          const isRunning = isCurrent && !isFailedHere && !isDoneOk;

          return (
            <li key={stage} className="flex flex-col items-center gap-1.5">
              <StageMarker
                isPast={isPast}
                isCurrent={isCurrent}
                isFuture={isFuture}
                isFailedHere={isFailedHere}
                isDoneOk={isDoneOk}
                isRunning={isRunning}
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
