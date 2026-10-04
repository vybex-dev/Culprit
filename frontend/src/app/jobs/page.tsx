// FILE: frontend/src/app/jobs/page.tsx
//
// History: every analysis this backend has run, newest first. Jobs are
// persisted (SQLite), so this survives a restart — and each row links straight
// back into the full dashboard, event log included.

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { listJobs } from "@/lib/api";
import { formatAgo, formatScore, repoDisplayName, shortSha } from "@/lib/format";
import { isTerminal, type JobSummary } from "@/lib/types";
import { LiveDot, Pill, Skeleton } from "@/components/ui";

function outcome(j: JobSummary): { label: string; variant: "neutral" | "iris" | "butter" | "resolved" | "unresolved" } {
  if (j.status === "failed") return { label: "Failed", variant: "unresolved" };
  if (j.status === "cancelled") return { label: "Cancelled", variant: "butter" };
  if (!isTerminal(j.status)) return { label: j.status[0].toUpperCase() + j.status.slice(1), variant: "iris" };
  if (j.final_result === "resolved") return { label: "Resolved", variant: "resolved" };
  if (j.regression_commit === null) return { label: "No regression", variant: "neutral" };
  return { label: "Diagnosed", variant: "butter" };
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const anyLive = jobs?.some((j) => !isTerminal(j.status)) ?? false;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const rows = await listJobs();
        if (cancelled) return;
        setJobs(rows);
        setError(null);
        // Keep the list fresh only while something is actually running.
        if (rows.some((j) => !isTerminal(j.status))) timer = setTimeout(load, 2500);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Couldn't load history.");
      }
    }
    load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return (
    <div className="mx-auto w-full max-w-5xl flex-1 px-4 py-10 sm:px-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-medium tracking-tight text-ink">History</h1>
          <p className="mt-1 text-sm text-muted">Every analysis this backend has run. Open one to replay its full event log.</p>
        </div>
        <Link href="/new" className="shrink-0 rounded-md bg-iris px-3.5 py-2 text-sm font-medium text-paper hover:opacity-90">
          New analysis
        </Link>
      </div>

      {error && (
        <div className="glow-unresolved mt-6 rounded-xl border border-transparent bg-unresolved/[0.08] px-4 py-3 text-sm text-ink">
          Couldn&apos;t reach the backend: <span className="font-mono text-xs">{error}</span>
        </div>
      )}

      {!error && jobs === null && (
        <div className="mt-6 space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      )}

      {jobs && jobs.length === 0 && (
        <div className="mt-10 rounded-xl border border-dashed border-line-strong px-6 py-12 text-center">
          <p className="text-sm text-ink">Nothing here yet.</p>
          <p className="mt-1 text-xs text-muted">Run the bundled demo to see a full analysis in about a minute.</p>
          <Link href="/new" className="mt-4 inline-block text-sm text-iris underline underline-offset-2">
            Start one →
          </Link>
        </div>
      )}

      {jobs && jobs.length > 0 && (
        <ul className="mt-6 divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
          {jobs.map((j) => {
            const o = outcome(j);
            const live = !isTerminal(j.status);
            return (
              <li key={j.job_id}>
                <Link href={`/job/${j.job_id}`} className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-4 py-3 transition-colors hover:bg-surface-2 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto]">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      {live && <LiveDot variant="iris" live />}
                      <span className="truncate text-sm font-medium text-ink">{j.label ?? repoDisplayName(j.repo_url)}</span>
                      {j.mode === "offline" && (
                        <span className="shrink-0 rounded bg-butter/30 px-1.5 py-px text-[10px] text-iris-700" title="Offline stand-in models; benchmarks real">
                          offline
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 truncate text-xs text-muted">
                      {j.subject ? <>“{j.subject}” · </> : null}
                      {j.regression_commit ? <span className="font-mono">{shortSha(j.regression_commit)}</span> : j.n_commits ? `${j.n_commits} commits` : "—"}
                      {j.category ? ` · ${j.category.replace(/_/g, " ")}` : ""}
                    </p>
                  </div>
                  <div className="hidden min-w-0 font-mono text-xs tabular-nums text-muted sm:block">
                    {j.before_score !== null && j.regressed_score !== null ? (
                      <span>
                        {formatScore(j.before_score)} → <span className="text-unresolved">{formatScore(j.regressed_score)}</span>
                        {j.after_score !== null && (
                          <>
                            {" → "}
                            <span className={clsx(j.final_result === "resolved" ? "text-resolved" : "text-muted")}>{formatScore(j.after_score)}</span>
                          </>
                        )}
                      </span>
                    ) : (
                      "—"
                    )}
                    {j.n_probes > 0 && <span className="ml-2 text-muted/70">{j.n_probes} probes</span>}
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <Pill variant={o.variant}>{o.label}</Pill>
                    <span className="text-[11px] text-muted/80">{formatAgo(j.created_at)}</span>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {anyLive && <p className="mt-3 text-center text-xs text-muted">Updating live while jobs are running…</p>}
    </div>
  );
}
