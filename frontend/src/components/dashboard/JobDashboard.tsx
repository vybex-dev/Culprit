// FILE: frontend/src/components/dashboard/JobDashboard.tsx

"use client";

import { useMemo, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import type { JobState } from "@/lib/types";
import { hasRegressionFound } from "@/lib/types";
import { shortSha } from "@/lib/format";
import { buildTrace } from "@/lib/trace";
import { JobHeader } from "./JobHeader";
import { StatusRibbon } from "./StatusRibbon";
import { MissionControl } from "./MissionControl";
import { TimelineChart } from "./TimelineChart";
import { RegressionCallout } from "./RegressionCallout";
import { DrillDownPanel } from "./DrillDownPanel";
import { DiagnosisSection, DiagnosisSectionLoading, type RegressionImpact } from "./DiagnosisSection";
import { FixSection } from "./FixSection";
import { FinalResultBanner } from "./FinalResultBanner";
import { TraceFeed } from "./TraceFeed";
import { ErrorBanner } from "./ErrorState";

/** Score at the last good commit vs. at the guilty one, from the real
 * timeline (oldest → newest). Null when there's no earlier measurement. */
function regressionImpact(job: JobState): RegressionImpact | null {
  const i = job.timeline.findIndex((e) => e.commit === job.regression_commit);
  return i > 0 ? { before: job.timeline[i - 1].score, after: job.timeline[i].score } : null;
}

const TERMINAL_STATUSES = new Set<JobState["status"]>(["done", "failed"]);

/** Staggered fade-up on first paint only (`initial` never re-applies on
 * later polls), so the dashboard assembles top to bottom instead of
 * popping in all at once. Skipped entirely for reduced-motion users. */
function Enter({ order, children }: { order: number; children: ReactNode }) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, delay: reduceMotion ? 0 : order * 0.05, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  );
}

export function JobDashboard({
  job,
  isReconnecting,
  clockOffsetMs = 0,
}: {
  job: JobState;
  isReconnecting?: boolean;
  clockOffsetMs?: number;
}) {
  const [panelRequestedOpen, setPanelRequestedOpen] = useState(false);
  const regressionFound = hasRegressionFound(job);
  // Derived, not synced via an effect: if a fixture swap or a fresh poll
  // ever drops the regression, the panel simply can't be open for it.
  const panelOpen = panelRequestedOpen && regressionFound;
  const isLive = !TERMINAL_STATUSES.has(job.status);

  // Recomputed on every render, but cheap (a few dozen entries at most)
  // and pure in `job` — see lib/trace.ts. useMemo just avoids re-deriving
  // on unrelated re-renders (e.g. panel open/close).
  const trace = useMemo(() => buildTrace(job), [job]);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6">
      <Enter order={0}>
        <JobHeader job={job} isReconnecting={isReconnecting} clockOffsetMs={clockOffsetMs} />
      </Enter>
      <Enter order={1}>
        <StatusRibbon job={job} />
      </Enter>

      {job.status === "failed" && (
        <Enter order={2}>
          <ErrorBanner error={job.error} />
        </Enter>
      )}
      {job.status === "done" && (
        <Enter order={2}>
          <FinalResultBanner job={job} />
        </Enter>
      )}

      <Enter order={3}>
        <MissionControl job={job} clockOffsetMs={clockOffsetMs} />
      </Enter>

      <Enter order={4}>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-start">
          <TimelineChart
            timeline={job.timeline}
            regressionCommit={job.regression_commit}
            onSelectRegression={() => setPanelRequestedOpen(true)}
            active={job.status === "bisecting"}
          />
          <TraceFeed events={trace} live={isLive} outcome={job.status === "failed" ? "stopped" : "finished"} />
        </div>
      </Enter>

      {regressionFound && job.regression_commit && (
        <Enter order={5}>
          <RegressionCallout
            timeline={job.timeline}
            regressionCommit={job.regression_commit}
            onOpen={() => setPanelRequestedOpen(true)}
          />
        </Enter>
      )}

      <DrillDownPanel
        open={panelOpen}
        onClose={() => setPanelRequestedOpen(false)}
        title={job.regression_commit ? `Commit ${shortSha(job.regression_commit)}` : "Regression"}
      >
        <div className="space-y-6">
          {job.diagnosis ? <DiagnosisSection diagnosis={job.diagnosis} impact={regressionImpact(job)} /> : <DiagnosisSectionLoading />}
          {job.diagnosis && <FixSection fix={job.fix} attempts={job.fix_attempts} />}
        </div>
      </DrillDownPanel>
    </div>
  );
}
