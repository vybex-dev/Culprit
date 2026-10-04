// FILE: frontend/src/components/dashboard/UnderTheHood.tsx
//
// What each part of the stack actually did on this job. Every number is summed
// from the real event stream (backend/events.py: summarize()) — calls, tokens
// and latency come from the API's own `usage` block, never estimated. Where the
// server doesn't report tokens, the tile says so instead of inventing a number.

import type { ReactNode } from "react";
import clsx from "clsx";
import { SectionLabel } from "@/components/ui";
import type { JobState, ModelMetrics } from "@/lib/types";

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div title={hint}>
      <dt className="text-[11px] text-muted">{label}</dt>
      <dd className="font-mono text-sm tabular-nums text-ink">{value}</dd>
    </div>
  );
}

function Tile({
  accent,
  title,
  role,
  detail,
  children,
  className,
}: {
  accent: "iris" | "butter" | "resolved" | "neutral";
  title: string;
  role: string;
  detail?: string;
  children: ReactNode;
  className?: string;
}) {
  const bar = { iris: "bg-iris", butter: "bg-butter-700", resolved: "bg-resolved", neutral: "bg-ink/25" }[accent];
  return (
    <div className={clsx("relative px-4 py-4 sm:px-5", className)}>
      <span className={clsx("absolute inset-x-4 top-0 h-0.5 rounded-full sm:inset-x-5", bar)} aria-hidden />
      <h3 className="text-sm font-medium text-ink">{title}</h3>
      <p className="mt-0.5 text-xs text-muted">{role}</p>
      {detail && <p className="mt-1 truncate font-mono text-[10px] text-muted/80" title={detail}>{detail}</p>}
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">{children}</dl>
    </div>
  );
}

function tokens(m?: ModelMetrics): string {
  if (!m || m.total_tokens === 0) return "n/a";
  return m.total_tokens >= 1000 ? `${(m.total_tokens / 1000).toFixed(1)}k` : String(m.total_tokens);
}

export function UnderTheHood({ job, className }: { job: JobState; className?: string }) {
  const m = job.metrics;
  if (!m) return null;
  const nano = m.models.nano;
  const ultra = m.models.ultra;
  const totalCalls = (nano?.calls ?? 0) + (ultra?.calls ?? 0);
  const offline = job.mode === "offline";
  const sb = m.sandbox;
  if (totalCalls === 0 && sb.runs === 0) return null;

  return (
    <section className={clsx("space-y-3", className)} aria-label="Under the hood">
      <SectionLabel>Under the hood — what ran this job</SectionLabel>
      <div className="grid grid-cols-1 divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-4 [&>*:nth-child(n+2)]:sm:border-l [&>*:nth-child(n+2)]:border-line">
        <Tile
          accent="iris"
          title={offline ? "Nano stand-in" : "Nemotron 3 Nano"}
          role="Fast judge — clean, regressed, or run it again, for every commit"
          detail={nano?.model_id}
        >
          <Stat label="calls" value={nano?.calls ?? 0} />
          <Stat label="avg latency" value={nano ? `${Math.round(nano.avg_latency_s * 1000)}ms` : "—"} />
          <Stat label="tokens" value={tokens(nano)} hint="From the API's usage block. n/a when the server reports none." />
          <Stat label="retries" value={nano?.retries ?? 0} hint="Replies that weren't valid JSON and were re-asked once" />
        </Tile>
        <Tile
          accent="butter"
          title={offline ? "Ultra stand-in" : "Nemotron 3 Ultra"}
          role="Deep reasoner — root cause with cited lines, then the fix"
          detail={ultra?.model_id}
        >
          <Stat label="calls" value={ultra?.calls ?? 0} hint="diagnose + each fix attempt" />
          <Stat label="avg latency" value={ultra ? `${ultra.avg_latency_s.toFixed(ultra.avg_latency_s < 10 ? 1 : 0)}s` : "—"} />
          <Stat label="tokens" value={tokens(ultra)} />
          <Stat label="retries" value={ultra?.retries ?? 0} />
        </Tile>
        <Tile
          accent="resolved"
          title={job.mode === "offline" ? "Local sandbox" : "Token Factory Sandboxes"}
          role="Real benchmarks, one fresh isolated environment per commit"
        >
          <Stat label="commits run" value={sb.probes} />
          <Stat label="benchmark runs" value={sb.runs} />
          <Stat label="on patched code" value={sb.patched_runs} hint="Runs that verify a proposed fix" />
          <Stat label="VMs" value={sb.instances || "—"} hint="Token Factory sandbox instances (0 for the local backend)" />
        </Tile>
        <Tile accent="neutral" title="Tavily" role="Grounds the diagnosis in public sources, filtered by relevance">
          <Stat label="searches" value={m.tavily.searches} />
          <Stat label="sources kept" value={m.tavily.sources} />
        </Tile>
      </div>
      {nano && ultra && totalCalls > 0 && (
        <p className="text-xs text-muted">
          Routing: the small model made{" "}
          <span className="font-medium text-ink">{Math.round((nano.calls / totalCalls) * 100)}%</span> of this job&apos;s model calls — the
          high-frequency judgements — so the large reasoning model is only spent on the {ultra.calls} steps that need it.
        </p>
      )}
    </section>
  );
}
