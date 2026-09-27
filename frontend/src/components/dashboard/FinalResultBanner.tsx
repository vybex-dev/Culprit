// FILE: frontend/src/components/dashboard/FinalResultBanner.tsx — place at this path in the Culprit repo

import clsx from "clsx";
import type { JobState } from "@/lib/types";
import { formatPctChange, formatScore } from "@/lib/format";
import { isVerifiedResolved } from "@/lib/types";

// CODE_REVIEW_FINDINGS.md #4: this used to collapse every non-resolved
// "done" job into one "Diagnosed, not auto-fixed" message — including two
// cases where no diagnosis exists at all (no regression found in range;
// regression found but not yet/never diagnosed), which claimed a
// "confirmed root cause" that was never produced. Each state below shows
// only what actually happened, per api.py's own module docstring flag #1
// for what "done" can legitimately mean.
type BannerVariant = "resolved" | "diagnosedNotFixed" | "noRegressionFound" | "regressionNotDiagnosed";

function variantFor(job: JobState): BannerVariant {
  if (isVerifiedResolved(job)) return "resolved";
  if (job.diagnosis) return "diagnosedNotFixed";
  if (job.regression_commit === null) return "noRegressionFound";
  return "regressionNotDiagnosed";
}

const VARIANT_STYLES: Record<BannerVariant, string> = {
  resolved: "border-resolved bg-resolved/[0.06] text-ink",
  diagnosedNotFixed: "border-butter-700 bg-butter-700/[0.12] text-ink",
  noRegressionFound: "border-resolved bg-resolved/[0.06] text-ink",
  regressionNotDiagnosed: "border-line-strong bg-paper text-ink",
};

export function FinalResultBanner({ job }: { job: JobState }) {
  if (job.status !== "done") return null;

  const variant = variantFor(job);

  return (
    <div
      className={clsx(
        "flex flex-wrap items-center gap-2 rounded-md border-l-4 px-4 py-3 text-sm",
        VARIANT_STYLES[variant],
      )}
    >
      {variant === "resolved" && job.fix ? (
        <span>
          <strong className="font-medium">Fix verified.</strong> Benchmark recovered from{" "}
          <span className="font-mono">{formatScore(job.fix.before_score)}</span> to{" "}
          <span className="font-mono">{formatScore(job.fix.after_score)}</span> (
          <span className="font-mono">{formatPctChange(job.fix.before_score, job.fix.after_score)}</span>), confirmed
          by a sandbox re-run.
        </span>
      ) : variant === "diagnosedNotFixed" ? (
        <span>
          <strong className="font-medium">Diagnosed, not auto-fixed.</strong> The root cause below is confirmed;
          {job.fix_attempts.length > 0
            ? ` ${job.fix_attempts.length} fix attempt${job.fix_attempts.length > 1 ? "s" : ""} did not bring the benchmark back within threshold.`
            : " no concrete fix was attempted (see the diagnosis category below)."}
        </span>
      ) : variant === "noRegressionFound" ? (
        <span>
          <strong className="font-medium">No regression found.</strong> Every commit in this range benchmarked
          within the regression threshold of the baseline — nothing to diagnose or fix.
        </span>
      ) : (
        <span>
          <strong className="font-medium">Regression found, not yet diagnosed.</strong> A guilty commit was
          identified, but no root-cause diagnosis is attached to this job.
        </span>
      )}
    </div>
  );
}
