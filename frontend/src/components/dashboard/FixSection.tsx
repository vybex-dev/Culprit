// FILE: frontend/src/components/dashboard/FixSection.tsx

import clsx from "clsx";
import type { Fix, FixAttempt, JobState } from "@/lib/types";
import { formatPctChange, formatScore } from "@/lib/format";
import { LiveDot, Pill, SectionLabel } from "@/components/ui";
import { DiffViewer } from "./DiffViewer";

function FixSummary({ fix, guard }: { fix: Fix; guard?: JobState["tests_guard"] }) {
  return (
    <div
      className={clsx(
        "flex flex-wrap items-center gap-3 rounded-xl border px-3 py-2.5 text-sm",
        fix.verified ? "border-transparent bg-resolved/[0.08] glow-resolved" : "border-line bg-surface",
      )}
    >
      <span className="font-mono text-muted">{formatScore(fix.before_score)}</span>
      <span className="text-muted" aria-hidden>
        →
      </span>
      <span className="font-mono text-ink">{formatScore(fix.after_score)}</span>
      <span className={clsx("font-mono text-xs", fix.after_score <= fix.before_score * 1.1 ? "text-resolved" : "text-unresolved")}>
        ({formatPctChange(fix.before_score, fix.after_score)})
      </span>
      <span className="ml-auto flex flex-wrap items-center gap-2">
        {/* Strictly sourced from fix.tests_verified / guard — never inferred. */}
        {fix.tests_verified === true && <Pill variant="resolved">Tests pass</Pill>}
        {fix.verified && fix.tests_verified == null && guard && guard !== "active" && (
          <span
            className="text-xs text-muted"
            title="The test command could not vouch for this patch, so it was verified on performance only."
          >
            performance-only
          </span>
        )}
        {/* Strictly sourced from fix.verified — never inferred from status or final_result. */}
        {fix.verified ? <Pill variant="resolved">Verified in sandbox</Pill> : <Pill variant="neutral">Not verified yet</Pill>}
      </span>
    </div>
  );
}

function AttemptRow({ attempt, best }: { attempt: FixAttempt; best: boolean }) {
  const inRound = attempt.candidate != null;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <LiveDot variant={attempt.resolved ? "resolved" : "unresolved"} />
        <span className="font-mono text-xs text-muted">
          {inRound ? `Round ${attempt.round ?? 1} · candidate ${attempt.candidate}` : `Attempt ${attempt.attempt}`}
        </span>
        {/* Strictly from attempt.selected: the verified candidate that was kept. */}
        {inRound && attempt.selected && <Pill variant="iris">{best ? "Kept — best verified" : "Kept"}</Pill>}
        <Pill variant={attempt.resolved ? "resolved" : "unresolved"}>
          {attempt.resolved
            ? "Verified"
            : attempt.error
              ? "Did not run cleanly"
              : attempt.tests_passed === false
                ? "Fast, but tests failed"
                : "Still regressed"}
        </Pill>
        {/* A crashed attempt never produced a measurement: no placeholder score. */}
        {!attempt.error && <span className="font-mono text-xs text-muted">{formatScore(attempt.score_after)}</span>}
      </div>
      {attempt.tests_passed === false && attempt.test_output && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">test output</summary>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-surface p-2 font-mono">
            {attempt.test_output}
          </pre>
        </details>
      )}
      <p className="text-sm leading-relaxed text-ink">{attempt.rationale}</p>
      <DiffViewer diff={attempt.patch} />
    </div>
  );
}

export function FixSection({
  fix,
  attempts,
  guard,
}: {
  fix: Fix | null;
  attempts: FixAttempt[];
  guard?: JobState["tests_guard"];
}) {
  if (attempts.length === 0) {
    return (
      <div className="space-y-3">
        <SectionLabel>Fix</SectionLabel>
        <p className="text-sm text-muted">No fix attempts yet.</p>
      </div>
    );
  }

  const verifiedCount = attempts.filter((a) => a.candidate != null && a.resolved).length;
  return (
    <div className="space-y-4">
      <SectionLabel>Fix</SectionLabel>
      {fix && <FixSummary fix={fix} guard={guard} />}
      {attempts.some((a) => a.candidate != null) && (
        <p className="text-xs leading-relaxed text-muted">
          Several different fixes were proposed per round and benchmarked. Only a verified one can be kept, and the best-measured
          verified one was.
        </p>
      )}
      <div className="space-y-5 divide-y divide-line">
        {attempts.map((attempt) => (
          <div key={attempt.attempt} className={attempt.attempt > 1 ? "pt-4" : ""}>
            <AttemptRow attempt={attempt} best={verifiedCount > 1} />
          </div>
        ))}
      </div>
    </div>
  );
}
