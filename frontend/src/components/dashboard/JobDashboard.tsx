// FILE: frontend/src/components/dashboard/JobDashboard.tsx

"use client";

import { useMemo, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import type { JobState, LogEvent } from "@/lib/types";
import { hasRegressionFound, isTerminal } from "@/lib/types";
import { shortSha } from "@/lib/format";
import { legacyEvents } from "@/lib/legacyEvents";
import { JobHeader } from "./JobHeader";
import { StatusRibbon } from "./StatusRibbon";
import { MissionControl } from "./MissionControl";
import { BisectScanner } from "./BisectScanner";
import { TimelineChart } from "./TimelineChart";
import { Terminal } from "./Terminal";
import { UnderTheHood } from "./UnderTheHood";
import { ExportBar } from "./ExportBar";
import { RegressionCallout } from "./RegressionCallout";
import { DrillDownPanel } from "./DrillDownPanel";
import { DiagnosisSection, DiagnosisSectionLoading, type RegressionImpact } from "./DiagnosisSection";
import { FixSection } from "./FixSection";
import { FinalResultBanner } from "./FinalResultBanner";
import { ErrorBanner } from "./ErrorState";
import { ActivitySignal } from "@/components/ActivitySignal";

/** Score before vs. at the guilty commit. "Before" is the job's BASELINE — the
 * same yardstick the Bisector thresholds against, and the number the scanner
 * and the PR report quote — so every surface agrees. Older jobs without a
 * recorded baseline fall back to the previous benchmarked commit. */
function regressionImpact(job: JobState): RegressionImpact | null {
  const i = job.timeline.findIndex((e) => e.commit === job.regression_commit);
  if (i < 0) return null;
  if (job.baseline_score != null) return { before: job.baseline_score, after: job.timeline[i].score };
  return i > 0 ? { before: job.timeline[i - 1].score, after: job.timeline[i].score } : null;
}

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

function CancelledBanner({ job }: { job: JobState }) {
  const probed = Math.max(0, (job.probes?.length ?? 0) - 1);
  return (
    <div className="flex items-start gap-2.5 rounded-xl border-l-4 border-butter-700 bg-butter-700/[0.1] px-4 py-3">
      <span className="mt-0.5 text-butter-700" aria-hidden>
        ■
      </span>
      <div>
        <p className="text-sm font-medium text-ink">Cancelled.</p>
        <p className="mt-1 text-xs text-muted">
          Stopped at a checkpoint after {probed} benchmarked commit{probed === 1 ? "" : "s"}. Everything measured up to that point is
          kept below — nothing was fabricated to fill the gap.
        </p>
      </div>
    </div>
  );
}

export function JobDashboard({
  job,
  events,
  isReconnecting,
  clockOffsetMs = 0,
}: {
  job: JobState;
  /** The job's live event stream. Omit for static fixtures (the state reference page). */
  events?: LogEvent[];
  isReconnecting?: boolean;
  clockOffsetMs?: number;
}) {
  const [panelRequestedOpen, setPanelRequestedOpen] = useState(false);
  const regressionFound = hasRegressionFound(job);
  // Derived, not synced via an effect: if a fixture swap or a fresh poll
  // ever drops the regression, the panel simply can't be open for it.
  const panelOpen = panelRequestedOpen && regressionFound;
  const isLive = !isTerminal(job.status);

  const isFixture = events === undefined;
  const logEvents = useMemo(() => (events ?? legacyEvents(job)), [events, job]);
  const hasProbes = (job.probes?.length ?? 0) > 0;
  const fix = job.fix ? { afterScore: job.fix.after_score, verified: job.fix.verified } : null;

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6">
      <ActivitySignal active={isLive} />
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
      {job.status === "cancelled" && (
        <Enter order={2}>
          <CancelledBanner job={job} />
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

      {(job.commits?.length ?? 0) > 0 && (
        <Enter order={4}>
          <BisectScanner job={job} />
        </Enter>
      )}

      <Enter order={5}>
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] xl:items-start">
          <TimelineChart
            timeline={job.timeline}
            regressionCommit={job.regression_commit}
            onSelectRegression={() => setPanelRequestedOpen(true)}
            active={job.status === "bisecting"}
            commits={job.commits}
            probes={hasProbes ? job.probes : undefined}
            baseline={job.baseline_score}
            thresholdPct={job.threshold_pct}
            fix={fix}
          />
          <Terminal
            events={logEvents}
            live={isLive && !isFixture}
            jobId={job.job_id}
            offline={job.mode === "offline"}
            clockOffsetMs={clockOffsetMs}
          />
        </div>
      </Enter>

      {regressionFound && job.regression_commit && (
        <Enter order={6}>
          <div className="space-y-4">
            <RegressionCallout
              timeline={job.timeline}
              regressionCommit={job.regression_commit}
              baseline={job.baseline_score}
              commit={job.commits?.find((c) => c.sha === job.regression_commit)}
              onOpen={() => setPanelRequestedOpen(true)}
            />
            {!isLive && <ExportBar job={job} />}
          </div>
        </Enter>
      )}

      <Enter order={7}>
        <UnderTheHood job={job} />
      </Enter>

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
