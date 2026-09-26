// FILE: frontend/src/components/dashboard/FinalResultBanner.tsx — place at this path in the Culprit repo

import clsx from "clsx";
import type { JobState } from "@/lib/types";
import { formatPctChange, formatScore } from "@/lib/format";
import { isVerifiedResolved } from "@/lib/types";

export function FinalResultBanner({ job }: { job: JobState }) {
  if (job.status !== "done") return null;

  const resolved = isVerifiedResolved(job);

  return (
    <div
      className={clsx(
        "flex flex-wrap items-center gap-2 rounded-md border-l-4 px-4 py-3 text-sm",
        resolved ? "border-resolved bg-resolved/[0.06] text-ink" : "border-butter-700 bg-butter-700/[0.12] text-ink",
      )}
    >
      {resolved && job.fix ? (
        <span>
          <strong className="font-medium">Fix verified.</strong> Benchmark recovered from{" "}
          <span className="font-mono">{formatScore(job.fix.before_score)}</span> to{" "}
          <span className="font-mono">{formatScore(job.fix.after_score)}</span> (
          <span className="font-mono">{formatPctChange(job.fix.before_score, job.fix.after_score)}</span>), confirmed
          by a sandbox re-run.
        </span>
      ) : (
        <span>
          <strong className="font-medium">Diagnosed, not auto-fixed.</strong> The root cause below is confirmed;
          {job.fix_attempts.length > 0
            ? ` ${job.fix_attempts.length} fix attempt${job.fix_attempts.length > 1 ? "s" : ""} did not bring the benchmark back within threshold.`
            : " no fix attempt resolved it."}
        </span>
      )}
    </div>
  );
}
