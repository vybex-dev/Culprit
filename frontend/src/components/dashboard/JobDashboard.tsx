// FILE: frontend/src/components/dashboard/JobDashboard.tsx — place at this path in the Culprit repo

"use client";

import { useState } from "react";
import type { JobState } from "@/lib/types";
import { hasRegressionFound } from "@/lib/types";
import { shortSha } from "@/lib/format";
import { JobHeader } from "./JobHeader";
import { StatusRibbon } from "./StatusRibbon";
import { TimelineChart } from "./TimelineChart";
import { RegressionCallout } from "./RegressionCallout";
import { DrillDownPanel } from "./DrillDownPanel";
import { DiagnosisSection, DiagnosisSectionLoading } from "./DiagnosisSection";
import { FixSection } from "./FixSection";
import { FinalResultBanner } from "./FinalResultBanner";
import { QueuedState } from "./EmptyState";
import { ErrorBanner } from "./ErrorState";

export function JobDashboard({ job, isReconnecting }: { job: JobState; isReconnecting?: boolean }) {
  const [panelRequestedOpen, setPanelRequestedOpen] = useState(false);
  const regressionFound = hasRegressionFound(job);
  // Derived, not synced via an effect: if a fixture swap or a fresh poll
  // ever drops the regression, the panel simply can't be open for it.
  const panelOpen = panelRequestedOpen && regressionFound;

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-6 py-8">
      <JobHeader job={job} isReconnecting={isReconnecting} />
      <StatusRibbon job={job} />

      {job.status === "failed" && <ErrorBanner error={job.error} />}
      <FinalResultBanner job={job} />

      {job.status === "queued" ? (
        <QueuedState />
      ) : (
        <TimelineChart
          timeline={job.timeline}
          regressionCommit={job.regression_commit}
          onSelectRegression={() => setPanelRequestedOpen(true)}
        />
      )}

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
