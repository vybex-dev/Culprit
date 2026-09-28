// FILE: frontend/src/components/dashboard/AgentCursor.tsx
//
// A small ghost pointer that travels between whichever part of Mission
// Control the pipeline is actually working on right now, with a label
// naming the real action. This is the literal "watch someone else's
// cursor move" moment — but it only ever moves in response to a real
// stage transition in job state (see lib/pipeline.ts's laneStatus),
// never on a decorative timer. It lives inside a `relative` container
// and positions itself by percentage, so it stays put across resizes.
//
// The label flips to the pointer's left past the container's midline so
// long trace text never runs off the right edge, and always truncates
// rather than wrapping or overflowing.
//
// Reduced-motion users don't get a moving pointer at all (a teleporting
// cursor is worse than none) — callers should still show the same
// information as plain text/status elsewhere, which every caller here
// already does via MissionControl's lane summaries.

"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import clsx from "clsx";

export interface CursorWaypoint {
  /** Changes whenever the cursor should "land" somewhere new — remounts
   * the ripple + label so they replay instead of just sliding text. */
  id: string;
  xPct: number;
  yPct: number;
  label: string;
}

function PointerGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden className="drop-shadow-sm">
      <path
        d="M2 1.3 13.6 7.6 7.9 8.7 5.4 14.2 2 1.3Z"
        fill="var(--butter)"
        stroke="var(--ink)"
        strokeWidth="0.9"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function AgentCursor({ waypoint }: { waypoint: CursorWaypoint | null }) {
  const reduceMotion = useReducedMotion();
  if (reduceMotion || !waypoint) return null;

  const flip = waypoint.xPct > 60;

  return (
    <motion.div
      className="pointer-events-none absolute z-20"
      style={{ left: 0, top: 0 }}
      initial={false}
      animate={{ left: `${waypoint.xPct}%`, top: `${waypoint.yPct}%` }}
      transition={{ type: "spring", stiffness: 110, damping: 16, mass: 0.7 }}
    >
      <div className="cursor-bob relative">
        <AnimatePresence>
          <motion.span
            key={waypoint.id}
            className="cursor-ripple absolute -inset-2 rounded-full border"
            style={{ borderColor: "var(--butter)" }}
            aria-hidden
          />
        </AnimatePresence>
        <PointerGlyph />
        <motion.div
          key={`${waypoint.id}-label`}
          initial={{ opacity: 0, y: -3 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
          className={clsx(
            "absolute top-3.5 inline-flex max-w-[13rem] items-center gap-1.5 whitespace-nowrap rounded-full border border-trace-line bg-trace-bg-raised px-2 py-0.5 font-mono text-[10px] font-medium text-trace-text shadow-lg",
            flip ? "right-0" : "left-3",
          )}
        >
          <span className="h-1 w-1 shrink-0 rounded-full bg-trace-accent" />
          <span className="truncate">{waypoint.label}</span>
        </motion.div>
      </div>
    </motion.div>
  );
}
