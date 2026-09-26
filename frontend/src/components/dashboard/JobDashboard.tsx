// FILE: frontend/src/components/dashboard/JobDashboard.tsx — place at this path in the Culprit repo

"use client";

import { useMemo, useState } from "react";
import type { JobState } from "@/lib/types";
import { hasRegressionFound } from "@/lib/types";
import { shortSha } from "@/lib/format";
import { buildTrace } from "@/lib/trace";
import { JobHeader } from "./JobHeader";
import { StatusRibbon } from "./StatusRibbon";
import { TimelineChart } from "./TimelineChart";
import { RegressionCallout } from "./RegressionCallout";
import { DrillDownPanel } from "./DrillDownPanel";
import { DiagnosisSection, DiagnosisSectionLoading } from "./DiagnosisSection";
import { FixSection } from "./FixSection";
import { FinalResultBanner } from "./FinalResultBanner";
import { TraceFeed } from "./TraceFeed";
import { ErrorBanner } from "./ErrorState";

const TERMINAL_STATUSES = new Set<JobState["status"]>(["done", "failed"]);

export function JobDashboard({ job, isReconnecting }: { job: JobState; isReconnecting?: boolean }) {
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
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-6 py-8">
      <JobHeader job={job} isReconnecting={isReconnecting} />
      <StatusRibbon job={job} />

      {job.status === "failed" && <ErrorBanner error={job.error} />}
      <FinalResultBanner job={job} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
        <TimelineChart
          timeline={job.timeline}
          regressionCommit={job.regression_commit}
          onSelectRegression={() => setPanelRequestedOpen(true)}
          active={job.status === "bisecting"}
        />
        <TraceFeed events={trace} live={isLive} className="lg:sticky lg:top-[76px]" />
      </div>

      {regressionFound && job.regression_commit && (
        <RegressionCallout
          timeline={job.timeline}
          regressionCommit={job.regression_commit}
          onOpen={() => setPanelRequestedOpen(true)}
        />
      )}

      <DrillDownPanel
        open={panelOpen}
        onClose={() => setPanelRequestedOpen(false)}
        title={job.regression_commit ? `Commit ${shortSha(job.regression_commit)}` : "Regression"}
      >
        <div className="space-y-6">
          {job.diagnosis ? <DiagnosisSection diagnosis={job.diagnosis} /> : <DiagnosisSectionLoading />}
          {job.diagnosis && <FixSection fix={job.fix} attempts={job.fix_attempts} />}
        </div>
      </DrillDownPanel>
    </div>
  );
}
