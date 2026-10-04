// FILE: frontend/src/components/dashboard/Terminal.tsx
//
// The live terminal. Everything in it is a REAL event from the pipeline
// (backend/events.py) — a git call that ran, a sandbox run that landed, a
// Nemotron request and its raw reply — rendered as it happens. It replaces the
// old TraceFeed, which could only re-describe the final job JSON.
//
// What makes it a terminal and not a log viewer:
//   * in-flight operations are live spinners with a running timer, and settle
//     into their result in place when the matching event arrives;
//   * benchmark runs fill in one by one as bars, colored against the real
//     baseline and threshold;
//   * every model call is expandable to the exact request and raw response —
//     nothing the agents decided is a black box;
//   * citation checks, Tavily sources and proposed patches expand inline.
//
// Row construction lives in lib/terminal.ts (pure, unit-tested).

"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import clsx from "clsx";
import { LiveDot } from "@/components/ui";
import { useNow } from "@/lib/clock";
import {
  FILTER_LABELS,
  buildRows,
  countByGroup,
  eventsToJsonl,
  eventsToText,
  filterRows,
  fmtDur,
  fmtMs,
  type Chip,
  type Detail,
  type Glyph,
  type RunGroup,
  type TermFilter,
  type TermRow,
  type Tone,
} from "@/lib/terminal";
import type { LogEvent } from "@/lib/types";
import { DiffViewer } from "./DiffViewer";

// Console-surface colors. The terminal is a fixed dark surface in both page
// themes (see globals.css), so these are fixed too — the page-theme greens/reds
// are tuned for light backgrounds and would be muddy here.
const TONE: Record<Tone, string> = {
  muted: "text-[var(--trace-muted)]",
  text: "text-[var(--trace-text)]",
  iris: "text-[var(--trace-iris)]",
  accent: "text-[var(--trace-accent)]",
  good: "text-[#2fe8c0]",
  bad: "text-[#ff6b6b]",
  warn: "text-[#ffd24d]",
};

const CHIP: Record<Tone, string> = {
  muted: "border-white/10 text-[var(--trace-muted)]",
  text: "border-white/15 text-[var(--trace-text)]",
  iris: "border-[var(--trace-iris)]/40 text-[var(--trace-iris)]",
  accent: "border-[var(--trace-accent)]/40 text-[var(--trace-accent)]",
  good: "border-[#2fe8c0]/40 text-[#2fe8c0]",
  bad: "border-[#ff6b6b]/40 text-[#ff6b6b]",
  warn: "border-[#ffd24d]/40 text-[#ffd24d]",
};

const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

function GlyphMark({ glyph, tone, frame }: { glyph: Glyph; tone: Tone; frame: number }) {
  const char: Record<Exclude<Glyph, "spin">, string> = {
    prompt: "$",
    ok: "✓",
    fail: "✗",
    dot: "·",
    arrow: "›",
    warn: "▲",
    star: "◆",
  };
  return (
    <span className={clsx("w-4 shrink-0 text-center", glyph === "spin" ? "text-[var(--trace-accent)]" : TONE[tone])} aria-hidden>
      {glyph === "spin" ? SPINNER[frame % SPINNER.length] : char[glyph]}
    </span>
  );
}

function ChipView({ chip }: { chip: Chip }) {
  return (
    <span
      title={chip.title}
      className={clsx("rounded border px-1.5 py-px text-[10px] leading-4", CHIP[chip.tone], chip.title && "cursor-help")}
    >
      {chip.label}
    </span>
  );
}

// ------------------------------------------------------------ run bars

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** The raw runs of one benchmark, as bars. Colored against the real baseline
 * and regression threshold, so "this run is slow" is visible at a glance. */
function RunBars({ runs, pending }: { runs: RunGroup; pending: boolean }) {
  const { scores, baseline, thresholdPct } = runs;
  const limit = baseline !== null && thresholdPct !== null ? baseline * (1 + thresholdPct / 100) : null;
  const top = Math.max(...scores, limit ?? 0, baseline ?? 0) || 1;
  const empty = Math.max(0, runs.nRuns - scores.length);
  const med = scores.length ? median(scores) : null;

  return (
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="inline-flex h-[18px] items-end gap-[3px]" aria-label={`${scores.length} of ${runs.nRuns} runs`}>
        {scores.map((s, i) => {
          const over = limit !== null && s > limit;
          return (
            <span
              key={i}
              title={fmtMs(s)}
              className={clsx("w-[6px] rounded-[1px]", limit === null ? "bg-[var(--trace-iris)]" : over ? "bg-[#ff6b6b]" : "bg-[#2fe8c0]")}
              style={{ height: `${Math.max(3, Math.round((s / top) * 18))}px` }}
            />
          );
        })}
        {pending &&
          Array.from({ length: empty }).map((_, i) => (
            <span key={`e${i}`} className="h-[3px] w-[6px] rounded-[1px] bg-white/10" />
          ))}
      </span>
      <span className="tabular-nums text-[var(--trace-muted)]">
        {scores.slice(0, 5).map((s) => (s < 1000 ? s.toFixed(1) : (s / 1000).toFixed(2) + "k")).join("  ")}
        {scores.length > 5 ? " …" : ""}
      </span>
      {med !== null && !pending && (
        <span className="text-[var(--trace-text)]">
          median <span className="tabular-nums">{fmtMs(med)}</span>
        </span>
      )}
    </span>
  );
}

// ---------------------------------------------------------- detail panes

function Pre({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <pre
      className={clsx(
        "trace-scroll max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-black/30 p-2 text-[11px] leading-[1.45] text-[var(--trace-text)]",
        className,
      )}
    >
      {children}
    </pre>
  );
}

function pretty(raw: string | null): string {
  if (raw === null) return "(waiting for the response…)";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function PaneLabel({ children }: { children: ReactNode }) {
  return <div className="mb-1 mt-2 first:mt-0 text-[10px] uppercase tracking-wide text-[var(--trace-muted)]">{children}</div>;
}

function DetailPane({ detail }: { detail: Detail }) {
  switch (detail.type) {
    case "model": {
      const req = detail.request as { system?: string; payload?: unknown } | null;
      const tok = detail.usage ? (detail.usage.prompt_tokens ?? 0) + (detail.usage.completion_tokens ?? 0) : 0;
      return (
        <div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[var(--trace-muted)]">
            <span>
              model <span className="text-[var(--trace-text)]">{detail.modelId}</span>
            </span>
            {detail.latencyS !== null && <span>latency {fmtDur(detail.latencyS)}</span>}
            {tok > 0 && (
              <span>
                tokens {detail.usage?.prompt_tokens ?? 0} in / {detail.usage?.completion_tokens ?? 0} out
              </span>
            )}
            {detail.attempts !== null && <span>attempts {detail.attempts}</span>}
          </div>
          {detail.offline && (
            <p className="mt-2 rounded border border-[#ffd24d]/30 bg-[#ffd24d]/5 px-2 py-1 text-[11px] text-[#ffd24d]">
              Offline stand-in: this reply came from Culprit&apos;s deterministic rules (backend/offline.py), not from Nemotron.
              Benchmark numbers elsewhere in this run are still real measurements.
            </p>
          )}
          <PaneLabel>Request — system prompt</PaneLabel>
          <Pre>{(req?.system ?? "").slice(0, 1800) + ((req?.system?.length ?? 0) > 1800 ? "\n…" : "")}</Pre>
          <PaneLabel>Request — payload</PaneLabel>
          <Pre>{JSON.stringify(req?.payload ?? null, null, 2)}</Pre>
          <PaneLabel>Response — raw</PaneLabel>
          <Pre>{pretty(detail.response)}</Pre>
        </div>
      );
    }
    case "citations":
      return (
        <div className="space-y-1">
          <p className="text-[11px] text-[var(--trace-muted)]">
            A diagnosis may only cite lines that actually appear as changes in the diff. Each citation is checked
            against it:
          </p>
          {detail.lines.map((l, i) => (
            <div key={i} className="flex gap-2 rounded bg-black/30 px-2 py-1 text-[11px]">
              <span className={l.verified ? "text-[#2fe8c0]" : "text-[#ff6b6b]"}>{l.verified ? "✓ in diff" : "✗ not in diff"}</span>
              <code className="min-w-0 break-words text-[var(--trace-text)]">{l.text.trim()}</code>
            </div>
          ))}
        </div>
      );
    case "tavily":
      return (
        <div className="space-y-2">
          <div className="text-[11px] text-[var(--trace-muted)]">
            query <code className="text-[var(--trace-text)]">{detail.query}</code>
          </div>
          {detail.refs.length === 0 && (
            <p className="text-[11px] text-[var(--trace-muted)]">
              Nothing relevant enough was returned, so no citation was forced.
            </p>
          )}
          {detail.refs.map((r) => (
            <div key={r.url} className="rounded bg-black/30 p-2">
              <div className="flex items-baseline justify-between gap-3">
                <a
                  href={r.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="min-w-0 truncate text-[12px] text-[var(--trace-iris)] underline-offset-2 hover:underline"
                >
                  {r.title}
                </a>
                {r.score !== null && (
                  <span className="flex shrink-0 items-center gap-1.5 text-[10px] text-[var(--trace-muted)]">
                    relevance
                    <span className="h-1 w-12 overflow-hidden rounded-full bg-white/10">
                      <span className="block h-full bg-[var(--trace-iris)]" style={{ width: `${Math.round(r.score * 100)}%` }} />
                    </span>
                    {r.score.toFixed(2)}
                  </span>
                )}
              </div>
              {r.snippet && <p className="mt-1 text-[11px] leading-snug text-[var(--trace-muted)]">{r.snippet}</p>}
            </div>
          ))}
          {detail.dropped > 0 && (
            <p className="text-[10px] text-[var(--trace-muted)]">{detail.dropped} result(s) below the relevance floor were dropped.</p>
          )}
        </div>
      );
    case "patch":
      return (
        <div>
          {detail.rationale && <p className="mb-2 text-[11px] text-[var(--trace-muted)]">{detail.rationale}</p>}
          <DiffViewer diff={detail.patch} />
        </div>
      );
    case "text":
      return <Pre>{detail.text || "(empty)"}</Pre>;
    case "json":
      return <Pre>{JSON.stringify(detail.value, null, 2)}</Pre>;
  }
}

// ----------------------------------------------------------------- rows

function RowView({
  row,
  t0,
  now,
  frame,
  expanded,
  onToggle,
}: {
  row: TermRow;
  t0: number;
  now: number;
  frame: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const startMs = Date.parse(row.ts);
  const rel = ((startMs - t0) / 1000).toFixed(1);
  const hasDetail = row.detail !== undefined;
  const liveElapsed = row.pending ? Math.max(0, (now - startMs) / 1000) : null;

  const body = (
    <div className="flex min-w-0 flex-1 items-baseline gap-2">
      <GlyphMark glyph={row.pending ? "spin" : row.glyph} tone={row.tone} frame={frame} />
      {/* Title, chips and run bars flow as ONE inline run, so a long title wraps
          within its own column instead of knocking the glyph onto its own line. */}
      <div className="min-w-0 flex-1 break-words">
        <span
          className={clsx(
            row.emphasis && "font-medium",
            row.pending ? "text-[var(--trace-text)]" : TONE[row.tone],
            row.glyph === "prompt" && "text-[var(--trace-text)]",
          )}
        >
          {row.title}
        </span>
        {row.chips?.map((c, i) => (
          <span key={i} className="ml-2 inline-block align-baseline">
            <ChipView chip={c} />
          </span>
        ))}
        {row.tag && (
          <span className="ml-2 inline-block rounded bg-[#ffd24d]/10 px-1.5 py-px align-baseline text-[10px] leading-4 text-[#ffd24d]">
            {row.tag}
          </span>
        )}
        {row.runs && (
          <span className="ml-2 inline-block align-middle">
            <RunBars runs={row.runs} pending={Boolean(row.pending)} />
          </span>
        )}
        {hasDetail && (
          <span className="ml-2 text-[10px] text-[var(--trace-muted)]" aria-hidden>
            {expanded ? "▾" : "▸"}
          </span>
        )}
      </div>
      {(row.meta || liveElapsed !== null) && (
        <span className="shrink-0 pl-2 tabular-nums text-[var(--trace-muted)]">
          {liveElapsed !== null ? `${liveElapsed.toFixed(1)}s` : row.meta}
        </span>
      )}
    </div>
  );

  return (
    <div className={clsx("term-row-in", row.depth === 1 && "ml-[3.25rem] border-l border-white/[0.07] pl-3")}>
      <div className="flex items-baseline gap-3 py-[3px]">
        {row.depth === 0 ? (
          <span className="w-12 shrink-0 text-right tabular-nums text-[10px] text-[var(--trace-muted)]/70">+{rel}s</span>
        ) : null}
        {hasDetail ? (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={expanded}
            className="flex min-w-0 flex-1 cursor-pointer rounded text-left hover:bg-white/[0.04]"
          >
            {body}
          </button>
        ) : (
          body
        )}
      </div>
      {expanded && row.detail && (
        <div className={clsx("mb-2 rounded-md border border-white/[0.07] bg-[var(--trace-bg-raised)] p-3", row.depth === 0 && "ml-[3.75rem]")}>
          <DetailPane detail={row.detail} />
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------- terminal

function download(filename: string, text: string, mime: string) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function Terminal({
  events,
  live,
  jobId,
  offline = false,
  clockOffsetMs = 0,
  className,
}: {
  events: LogEvent[];
  /** The job is still running (drives the live indicator and timers). */
  live: boolean;
  jobId: string;
  offline?: boolean;
  clockOffsetMs?: number;
  className?: string;
}) {
  const [filter, setFilter] = useState<TermFilter>("all");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [follow, setFollow] = useState(true);
  const [copied, setCopied] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  const allRows = useMemo(() => buildRows(events), [events]);
  const rows = useMemo(() => filterRows(allRows, filter), [allRows, filter]);
  const counts = useMemo(() => countByGroup(allRows), [allRows]);
  const anyPending = allRows.some((r) => r.pending);

  // One shared timer drives every spinner and live elapsed counter.
  const now = useNow(live && anyPending, 100) + clockOffsetMs;
  const frame = Math.floor(now / 90);

  const t0 = events.length ? Date.parse(events[0].ts) : 0;
  const tLast = events.length ? Date.parse(events[events.length - 1].ts) : 0;
  const span = events.length ? (live ? Math.max(tLast, now) : tLast) - t0 : 0;

  // Keep the newest row in view while following; stop following the moment the
  // user scrolls up to read, and offer a one-click way back.
  useEffect(() => {
    const el = scroller.current;
    if (el && follow) el.scrollTop = el.scrollHeight;
  }, [rows.length, follow, anyPending]);

  function onScroll() {
    const el = scroller.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setFollow((f) => (f === nearBottom ? f : nearBottom));
  }

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function copyLog() {
    try {
      await navigator.clipboard.writeText(eventsToText(events));
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard can be denied — a convenience, not a requirement */
    }
  }

  const tokens = events.reduce((sum, e) => {
    if (e.kind !== "model.end") return sum;
    const u = (e.data.usage ?? {}) as { prompt_tokens?: number; completion_tokens?: number };
    return sum + (u.prompt_tokens ?? 0) + (u.completion_tokens ?? 0);
  }, 0);

  return (
    <section
      aria-label="Live terminal"
      // Fixed height (matches the chart card beside it): the log scrolls inside the box
      // instead of the box growing/shrinking with the number of rows.
      className={clsx("flex h-[440px] min-h-0 flex-col overflow-hidden rounded-xl border border-white/[0.07] bg-[var(--trace-bg)] font-mono text-[12px] leading-[1.5] text-[var(--trace-text)]", className)}
    >
      <header className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-white/[0.07] px-3 py-2">
        <span className="flex items-center gap-2 text-[11px] text-[var(--trace-muted)]">
          <LiveDot variant={live ? "butter" : "neutral"} live={live} />
          <span className="text-[var(--trace-text)]">{live ? "live" : "recorded"}</span>
          <span className="hidden sm:inline">{live ? "· real events, as they happen" : "· the full event log of this run"}</span>
        </span>
        {offline && (
          <span className="rounded bg-[#ffd24d]/10 px-1.5 py-px text-[10px] text-[#ffd24d]" title="Model calls in this run are a labelled stand-in; benchmarks are real.">
            offline stand-in models
          </span>
        )}
        <div className="trace-scroll -mx-1 flex max-w-full items-center gap-1 overflow-x-auto px-1 sm:ml-auto" role="tablist" aria-label="Filter log">
          {FILTER_LABELS.map(({ value, label }) => {
            const n = value === "all" ? allRows.length : counts[value];
            const active = filter === value;
            return (
              <button
                key={value}
                role="tab"
                aria-selected={active}
                type="button"
                onClick={() => setFilter(value)}
                className={clsx(
                  "shrink-0 rounded px-2 py-0.5 text-[11px] transition-colors",
                  active ? "bg-white/12 text-[var(--trace-text)]" : "text-[var(--trace-muted)] hover:bg-white/[0.06] hover:text-[var(--trace-text)]",
                )}
              >
                {label} <span className="tabular-nums opacity-60">{n}</span>
              </button>
            );
          })}
        </div>
      </header>

      <div
        ref={scroller}
        onScroll={onScroll}
        className="trace-scroll relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-3 py-2"
        role="log"
        aria-live="off"
      >
        {rows.length === 0 ? (
          <p className="py-8 text-center text-[var(--trace-muted)]">
            {live ? "Waiting for the first event" : "No events recorded for this job"}
            {live && <span className="trace-caret ml-0.5 text-[var(--trace-accent)]">▋</span>}
          </p>
        ) : (
          rows.map((row) => (
            <RowView
              key={row.id}
              row={row}
              t0={t0}
              now={now}
              frame={frame}
              expanded={expanded.has(row.id)}
              onToggle={() => toggle(row.id)}
            />
          ))
        )}
        {live && rows.length > 0 && !anyPending && (
          <div className="py-1 pl-[3.75rem] text-[var(--trace-accent)]">
            <span className="trace-caret">▋</span>
          </div>
        )}
      </div>

      <footer className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-t border-white/[0.07] px-3 py-1.5 text-[10px] text-[var(--trace-muted)]">
        <span className="tabular-nums">{events.length} events</span>
        {events.length > 0 && <span className="tabular-nums">{fmtDur(span / 1000)}</span>}
        {tokens > 0 && <span className="tabular-nums">{tokens.toLocaleString()} tokens</span>}
        <span className="ml-auto flex items-center gap-1">
          {!follow && live && (
            <button
              type="button"
              onClick={() => setFollow(true)}
              className="rounded bg-[var(--trace-accent)]/15 px-2 py-0.5 text-[var(--trace-accent)] hover:bg-[var(--trace-accent)]/25"
            >
              ↓ jump to latest
            </button>
          )}
          <button type="button" onClick={copyLog} disabled={!events.length} className="w-[4.5rem] rounded px-2 py-0.5 text-center hover:bg-white/[0.06] hover:text-[var(--trace-text)] disabled:opacity-40">
            {copied ? "copied" : "copy log"}
          </button>
          <button
            type="button"
            disabled={!events.length}
            onClick={() => download(`culprit-${jobId}.jsonl`, eventsToJsonl(events), "application/x-ndjson")}
            className="rounded px-2 py-0.5 hover:bg-white/[0.06] hover:text-[var(--trace-text)] disabled:opacity-40"
            title="Full-fidelity log: every request and raw response"
          >
            download .jsonl
          </button>
        </span>
      </footer>
    </section>
  );
}
