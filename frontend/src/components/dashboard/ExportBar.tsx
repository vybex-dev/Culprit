// FILE: frontend/src/components/dashboard/ExportBar.tsx
//
// Take the result somewhere useful: a PR-ready write-up (rendered by the
// backend from the recorded job — it can only claim what the job proved) and a
// real .patch file you can `git apply`.

"use client";

import { useState } from "react";
import { fetchReportMarkdown, patchUrl, reportUrl } from "@/lib/api";
import type { JobState } from "@/lib/types";

function Btn({ children, onClick, href, title }: { children: React.ReactNode; onClick?: () => void; href?: string; title?: string }) {
  const cls =
    "inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-line-strong hover:bg-surface-2";
  return href ? (
    <a href={href} className={cls} title={title} download>
      {children}
    </a>
  ) : (
    <button type="button" onClick={onClick} className={cls} title={title}>
      {children}
    </button>
  );
}

export function ExportBar({ job }: { job: JobState }) {
  const [state, setState] = useState<"idle" | "copied" | "error">("idle");
  const hasFix = job.fix !== null && job.fix !== undefined;
  if (!job.regression_commit) return null;

  async function copyPr() {
    try {
      await navigator.clipboard.writeText(await fetchReportMarkdown(job.job_id));
      setState("copied");
    } catch {
      setState("error");
    }
    setTimeout(() => setState("idle"), 1800);
  }

  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Export">
      <span className="mr-1 text-xs text-muted">Take it with you</span>
      <Btn onClick={copyPr} title="Markdown write-up: numbers, root cause, verified patch, evidence table">
        {state === "copied" ? "Copied ✓" : state === "error" ? "Couldn't copy" : "Copy PR description"}
      </Btn>
      <Btn href={reportUrl(job.job_id)} title="Download the write-up as report.md">
        Download report.md
      </Btn>
      {hasFix && (
        <Btn href={patchUrl(job.job_id)} title={job.fix?.verified ? "A patch verified by a sandbox re-run" : "Unverified — review before applying"}>
          Download .patch{job.fix?.verified ? "" : " (unverified)"}
        </Btn>
      )}
    </div>
  );
}
