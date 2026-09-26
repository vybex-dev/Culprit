// FILE: frontend/src/components/dashboard/FixSection.tsx — place at this path in the Culprit repo

import clsx from "clsx";
import type { Fix, FixAttempt } from "@/lib/types";
import { formatPctChange, formatScore } from "@/lib/format";
import { Pill, SectionLabel } from "@/components/ui";
import { DiffViewer } from "./DiffViewer";

function FixSummary({ fix }: { fix: Fix }) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-md border border-line px-3 py-2.5 text-sm">
      <span className="font-mono text-muted">{formatScore(fix.before_score)}</span>
      <span className="text-muted" aria-hidden>
        →
      </span>
      <span className="font-mono text-ink">{formatScore(fix.after_score)}</span>
      <span
        className={clsx(
          "font-mono text-xs",
          fix.after_score <= fix.before_score * 1.1 ? "text-resolved" : "text-unresolved",
        )}
      >
        ({formatPctChange(fix.before_score, fix.after_score)})
      </span>
      <span className="ml-auto">
        {/* Strictly sourced from fix.verified — never inferred from status or final_result. */}
        {fix.verified ? <Pill variant="resolved">Verified in sandbox</Pill> : <Pill variant="neutral">Not verified yet</Pill>}
      </span>
    </div>
  );
}

function AttemptRow({ attempt }: { attempt: FixAttempt }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted">Attempt {attempt.attempt}</span>
        <Pill variant={attempt.resolved ? "resolved" : "unresolved"}>
          {attempt.resolved ? "Resolved" : "Still regressed"}
        </Pill>
        <span className="font-mono text-xs text-muted">{formatScore(attempt.score_after)}</span>
      </div>
      <p className="text-sm leading-relaxed text-ink">{attempt.rationale}</p>
      <DiffViewer diff={attempt.patch} />
    </div>
  );
}

export function FixSection({ fix, attempts }: { fix: Fix | null; attempts: FixAttempt[] }) {
  if (attempts.length === 0) {
    return (
      <div className="space-y-3">
        <SectionLabel>Fix</SectionLabel>
        <p className="text-sm text-muted">No fix attempts yet.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <SectionLabel>Fix</SectionLabel>
      {fix && <FixSummary fix={fix} />}
      <div className="space-y-5 divide-y divide-line">
        {attempts.map((attempt) => (
          <div key={attempt.attempt} className={attempt.attempt > 1 ? "pt-4" : ""}>
            <AttemptRow attempt={attempt} />
          </div>
        ))}
      </div>
    </div>
  );
}
