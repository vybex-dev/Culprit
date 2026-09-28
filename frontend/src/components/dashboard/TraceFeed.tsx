// FILE: frontend/src/components/dashboard/TraceFeed.tsx
//
// The live activity feed: a terminal-styled log of what the pipeline has
// actually done so far, derived from real job state via lib/trace.ts.
// New events stream in one at a time (typed, not pasted) as they appear —
// this is the "watch the agent work" moment the rest of the dashboard
// builds around.
//
// Streaming is purely a presentation delay on events that already
// happened — nothing here invents an event before its underlying job
// state exists.
//
// Fixed size, scrolling body. The terminal has a constant height (header
// + scrolling log + status bar) so it never pushes the page around as
// lines arrive; overflow scrolls inside it. Scroll behavior follows the
// established "smart follow" log-viewer pattern:
//   - while you're at the bottom it follows the newest line;
//   - scrolling up past DETACH_PX pauses following, so reading history
//     is never yanked away from you (the threshold also tolerates the
//     list growing by a line, which is what makes programmatic scrolling
//     safe to tell apart from yours);
//   - a "Jump to latest" button appears, with a count of lines that
//     arrived while you were away;
//   - getting back within REATTACH_PX of the bottom resumes following.

"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { TraceEvent, TraceKind } from "@/lib/trace";
import { LiveDot } from "@/components/ui";

const DETACH_PX = 80;
const REATTACH_PX = 24;

type Filter = "all" | "milestones";
const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "milestones", label: "Milestones" },
];

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

function Line({ event }: { event: TraceEvent }) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, y: -4 }}
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
  outcome = "finished",
  startEmpty = false,
  controls = true,
  announce = true,
  heightClassName = "h-80",
  className,
}: {
  events: TraceEvent[];
  /** false disables the type-in streaming (e.g. static state-reference view) */
  live?: boolean;
  /** How to label a feed that has stopped streaming. */
  outcome?: "finished" | "stopped";
  /** true always types every event in from scratch on mount, instead of
   * showing already-known history immediately and only streaming what
   * arrives after — use for a looping demo (landing page hero), not for
   * a real job's first paint. */
  startEmpty?: boolean;
  /** Show the filter and the status bar. Off for the decorative hero demo. */
  controls?: boolean;
  /** Announce new lines to screen readers. Off for the looping hero demo,
   * which would otherwise read itself out forever. */
  announce?: boolean;
  /** Fixed height (a Tailwind class). It never grows with content. */
  heightClassName?: string;
  className?: string;
}) {
  const shown = useStreamedEvents(events, live, startEmpty);
  const reduceMotion = useReducedMotion();
  const scrollRef = useRef<HTMLDivElement>(null);
  // Scroll events caused by our own smooth "jump" shouldn't be mistaken
  // for the user scrolling away; ignore them briefly.
  const ignoreScrollUntil = useRef(0);

  const [filter, setFilter] = useState<Filter>("all");
  const [following, setFollowing] = useState(true);
  const [canScrollUp, setCanScrollUp] = useState(false);
  // How many visible lines existed when the user scrolled away — lets us
  // show "N new" without an effect syncing state.
  const [seenCount, setSeenCount] = useState(0);

  const visible = filter === "all" ? shown : shown.filter((e) => e.kind !== "score");
  const hidden = shown.length - visible.length;
  const unseen = following ? 0 : Math.max(0, visible.length - seenCount);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollUp(el.scrollTop > 4);
    if (performance.now() < ignoreScrollUntil.current) return;

    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (following && distanceFromBottom > DETACH_PX) {
      setFollowing(false);
      setSeenCount(visible.length);
    } else if (!following && distanceFromBottom <= REATTACH_PX) {
      setFollowing(true);
    }
  }

  // Follow the tail. Instant (not smooth) on purpose: each line already
  // animates in, and chaining smooth scrolls at ~8 lines/second lags.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !following) return;
    if (performance.now() < ignoreScrollUntil.current) return;
    el.scrollTop = el.scrollHeight;
  }, [visible.length, following, filter]);

  function jumpToLatest() {
    const el = scrollRef.current;
    if (!el) return;
    ignoreScrollUntil.current = performance.now() + 600;
    setFollowing(true);
    el.scrollTo({ top: el.scrollHeight, behavior: reduceMotion ? "auto" : "smooth" });
  }

  function changeFilter(next: Filter) {
    setFilter(next);
    setFollowing(true);
  }

  return (
    <div
      className={clsx(
        "flex flex-col overflow-hidden rounded-xl border border-trace-line bg-trace-bg shadow-[0_1px_0_rgba(255,255,255,0.04)_inset] transition-shadow",
        heightClassName,
        live && "glow-butter",
        className,
      )}
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-trace-line bg-trace-bg-raised px-3.5 py-2">
        <LiveDot variant="butter" live={live} />
        <span className="font-mono text-[11px] tracking-wide text-trace-muted">Agent activity</span>
        {controls ? (
          <div
            role="group"
            aria-label="Filter activity"
            className="ml-auto flex items-center gap-0.5 rounded-full border border-trace-line p-0.5"
          >
            {FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                aria-pressed={filter === f.value}
                onClick={() => changeFilter(f.value)}
                className={clsx(
                  "rounded-full px-2 py-0.5 font-mono text-[10px] transition-colors",
                  filter === f.value ? "bg-white/[0.14] text-trace-text" : "text-trace-muted hover:text-trace-text",
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
        ) : (
          live && <span className="ml-auto font-mono text-[10px] text-trace-muted/70">watching…</span>
        )}
      </div>

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          role="log"
          aria-live={announce ? "polite" : "off"}
          aria-label="Agent activity"
          tabIndex={0}
          className="trace-scroll h-full overflow-y-auto overscroll-contain px-3.5 py-2.5 font-mono text-[12.5px]"
        >
          <AnimatePresence initial={false}>
            {visible.map((event) => (
              <Line key={event.id} event={event} />
            ))}
          </AnimatePresence>
          {live && (
            <span
              className="trace-caret ml-[22px] inline-block h-[13px] w-[7px] translate-y-[2px] bg-trace-accent"
              aria-hidden
            />
          )}
        </div>

        {/* Edge fades hint that there's more above / below. */}
        <div
          aria-hidden
          className={clsx(
            "pointer-events-none absolute inset-x-0 top-0 h-6 bg-gradient-to-b from-trace-bg to-transparent transition-opacity duration-200",
            canScrollUp ? "opacity-100" : "opacity-0",
          )}
        />
        <div
          aria-hidden
          className={clsx(
            "pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-trace-bg to-transparent transition-opacity duration-200",
            following ? "opacity-0" : "opacity-100",
          )}
        />

        <div className="pointer-events-none absolute inset-x-0 bottom-2.5 flex justify-center">
          <AnimatePresence>
            {!following && (
              <motion.button
                type="button"
                onClick={jumpToLatest}
                initial={reduceMotion ? false : { opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 6 }}
                transition={{ duration: 0.18, ease: "easeOut" }}
                className="pointer-events-auto flex items-center gap-1.5 rounded-full border border-trace-line bg-trace-bg-raised px-3 py-1 font-mono text-[11px] text-trace-text shadow-lg transition-colors hover:bg-white/[0.12]"
              >
                <span aria-hidden>↓</span>
                Jump to latest
                {unseen > 0 && (
                  <span className="rounded-full bg-trace-accent px-1.5 text-[10px] font-semibold text-[var(--cite-text)]">
                    {unseen} new
                  </span>
                )}
              </motion.button>
            )}
          </AnimatePresence>
        </div>
      </div>

      {controls && (
        <div className="flex shrink-0 items-center justify-between border-t border-trace-line bg-trace-bg-raised px-3.5 py-1.5 font-mono text-[10px] text-trace-muted">
          <span className="flex items-center gap-1.5">
            {live ? (
              <>
                <LiveDot variant="butter" live />
                streaming
              </>
            ) : (
              <>
                <span aria-hidden className={outcome === "stopped" ? "text-unresolved" : "text-trace-accent"}>
                  {outcome === "stopped" ? "✕" : "✓"}
                </span>
                {outcome}
              </>
            )}
          </span>
          <span>
            {visible.length} {visible.length === 1 ? "event" : "events"}
            {hidden > 0 && ` (${hidden} hidden)`}
          </span>
        </div>
      )}
    </div>
  );
}
