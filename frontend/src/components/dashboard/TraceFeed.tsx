// FILE: frontend/src/components/dashboard/TraceFeed.tsx
//
// The live activity feed: a terminal-styled log of what the pipeline has
// actually done so far, derived from real job state via lib/trace.ts.
// New events stream in one at a time (typed, not pasted) as they appear —
// this is the "watch the agent work" moment the rest of the dashboard
// builds around, in the spirit of an agentic coding tool's visible
// activity trace rather than a silent progress bar.
//
// Streaming is purely a presentation delay on events that already
// happened — nothing here invents an event before its underlying job
// state exists. If two polls both bring 5 new lines, this feed still
// only ever shows lines that correspond to real data.

"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { TraceEvent, TraceKind } from "@/lib/trace";
import { LiveDot } from "@/components/ui";

const KIND_GLYPH: Record<TraceKind, string> = {
  system: "›",
  sandbox: "◆",
  score: "·",
  regression: "▲",
  model: "✦",
  diagnosis: "✓",
  patch: "▸",
  verify: "◆",
  done: "✓",
  error: "✕",
};

const KIND_COLOR: Record<TraceKind, string> = {
  system: "text-trace-muted",
  sandbox: "text-trace-iris",
  score: "text-trace-muted",
  regression: "text-trace-accent",
  model: "text-trace-iris",
  diagnosis: "text-trace-accent",
  patch: "text-trace-iris",
  verify: "text-trace-iris",
  done: "text-trace-accent",
  error: "text-unresolved",
};

function Line({ event, isNew }: { event: TraceEvent; isNew: boolean }) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      initial={isNew && !reduceMotion ? { opacity: 0, y: -4 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      className="flex items-baseline gap-2.5 py-[3px] leading-[1.5]"
    >
      <span className={clsx("w-3 shrink-0 text-center text-[11px]", KIND_COLOR[event.kind])} aria-hidden>
        {KIND_GLYPH[event.kind]}
      </span>
      <span className="min-w-0 flex-1">
        <span className="text-trace-text">{event.text}</span>
        {event.detail && <span className="text-trace-muted"> — {event.detail}</span>}
      </span>
    </motion.div>
  );
}

/**
 * Streams `events` in one line at a time when new ones arrive, instead of
 * dumping the whole array in on first paint. On mount, if this is the
 * first render for the job, everything already present shows immediately
 * (no fake replay of history); only events that arrive after mount stream.
 */
function useStreamedEvents(events: TraceEvent[], live: boolean, startEmpty: boolean) {
  const [shown, setShown] = useState<TraceEvent[]>(startEmpty ? [] : events);
  const shownIdsRef = useRef<Set<string>>(new Set(startEmpty ? [] : events.map((e) => e.id)));
  const queueRef = useRef<TraceEvent[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reduceMotion = useReducedMotion();

  // Append without ever repeating an id. React keys the feed by event id,
  // so a repeat isn't just cosmetic — it renders the same line twice.
  function commit(batch: TraceEvent[]) {
    const fresh = batch.filter((e) => !shownIdsRef.current.has(e.id));
    if (fresh.length === 0) return;
    fresh.forEach((e) => shownIdsRef.current.add(e.id));
    setShown((prev) => {
      const have = new Set(prev.map((e) => e.id));
      const add = fresh.filter((e) => !have.has(e.id));
      return add.length === 0 ? prev : [...prev, ...add];
    });
  }

  useEffect(() => {
    const pending = events.filter((e) => !shownIdsRef.current.has(e.id));
    if (pending.length === 0) return;

    if (!live || reduceMotion) {
      commit(pending);
      return;
    }

    // The queue is *derived*, not accumulated: `pending` is by definition
    // every event not yet shown, in order — including anything an earlier
    // run queued but never got to. Appending to the old queue instead would
    // re-add those events (this effect re-runs on every poll, and twice on
    // mount under Strict Mode) and stream each one out twice.
    queueRef.current = pending;

    function pump() {
      const next = queueRef.current.shift();
      if (!next) {
        timerRef.current = null;
        return;
      }
      commit([next]);
      timerRef.current = setTimeout(pump, 130);
    }

    pump();

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  return shown;
}

export function TraceFeed({
  events,
  live = true,
  startEmpty = false,
  className,
}: {
  events: TraceEvent[];
  /** false disables the type-in streaming (e.g. static state-reference view) */
  live?: boolean;
  /** true always types every event in from scratch on mount, instead of
   * showing already-known history immediately and only streaming what
   * arrives after — use for a looping demo (landing page hero), not for
   * a real job's first paint. */
  startEmpty?: boolean;
  className?: string;
}) {
  const shown = useStreamedEvents(events, live, startEmpty);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [shown.length]);

  return (
    <div
      className={clsx(
        "overflow-hidden rounded-xl border border-trace-line bg-trace-bg shadow-[0_1px_0_rgba(255,255,255,0.04)_inset] transition-shadow",
        live && "glow-butter",
        className,
      )}
    >
      <div className="flex items-center gap-2 border-b border-trace-line bg-trace-bg-raised px-3.5 py-2">
        <LiveDot variant="butter" live={live} />
        <span className="font-mono text-[11px] tracking-wide text-trace-muted">Agent activity</span>
        {live && <span className="ml-auto font-mono text-[10px] text-trace-muted/70">watching…</span>}
      </div>
      <div ref={scrollRef} className="trace-scroll max-h-64 overflow-y-auto px-3.5 py-2.5 font-mono text-[12.5px]">
        <AnimatePresence initial={false}>
          {shown.map((event) => (
            <Line key={event.id} event={event} isNew />
          ))}
        </AnimatePresence>
        {live && (
          <span
            className="trace-caret ml-[22px] inline-block h-[13px] w-[7px] translate-y-[2px] bg-trace-accent"
            aria-hidden
          />
        )}
      </div>
    </div>
  );
}
