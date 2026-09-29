// FILE: frontend/src/components/dashboard/TimelineChart.tsx

"use client";

import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { motion, useReducedMotion } from "motion/react";
import type { TimelineEntry } from "@/lib/types";
import { formatScore, formatTimestamp, shortSha } from "@/lib/format";

interface ChartPoint extends TimelineEntry {
  isRegression: boolean;
}

interface DotRenderProps {
  cx?: number;
  cy?: number;
  payload?: ChartPoint;
}

interface TooltipRenderProps {
  active?: boolean;
  payload?: Array<{ payload: ChartPoint }>;
}

function ChartTooltip({ active, payload }: TooltipRenderProps) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-lg">
      <div className="font-mono text-ink">{shortSha(point.commit)}</div>
      <div className="mt-0.5 text-muted">{formatTimestamp(point.timestamp)}</div>
      <div className="mt-1 font-mono font-medium text-ink">{formatScore(point.score)}</div>
      {point.isRegression && <div className="mt-1 text-iris-700">Regression commit — click to open</div>}
    </div>
  );
}

function makeDot(onSelectRegression: () => void) {
  function Dot({ cx, cy, payload }: DotRenderProps) {
    if (cx == null || cy == null || !payload) return null;
    if (!payload.isRegression) {
      return <circle cx={cx} cy={cy} r={3} fill="var(--iris)" />;
    }
    return (
      <g
        style={{ cursor: "pointer" }}
        onClick={onSelectRegression}
        role="button"
        tabIndex={0}
        aria-label={`Open diagnosis for commit ${shortSha(payload.commit)}`}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") onSelectRegression();
        }}
      >
        {/* Pulses via a CSS scale transform rather than animating the SVG
            `r` attribute directly — Motion can momentarily hand the DOM an
            undefined r mid-tween, which the browser rejects as an invalid
            attribute ("Expected length, undefined"). Scale is always a
            plain number, so it can't hit that state. */}
        <motion.circle
          cx={cx}
          cy={cy}
          r={9}
          fill="var(--butter)"
          style={{ transformOrigin: `${cx}px ${cy}px` }}
          initial={{ scale: 1, opacity: 0.35 }}
          animate={{ scale: [1, 16 / 9, 1], opacity: [0.35, 0.05, 0.35] }}
          transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
        />
        <circle cx={cx} cy={cy} r={5.5} fill="var(--butter)" stroke="var(--iris-700)" strokeWidth={1.5} />
      </g>
    );
  }
  return Dot;
}

function EmptyChart({ active }: { active: boolean }) {
  return (
    <div className="flex h-80 flex-col items-center justify-center gap-2.5 rounded-md border border-dashed border-line-strong text-center">
      <span className="relative flex h-2 w-2" aria-hidden>
        {active && <span className="breathe-ring absolute inset-0 rounded-full bg-iris" />}
        <span className="relative h-2 w-2 rounded-full bg-iris" />
      </span>
      <p className="text-sm text-ink">{active ? "Starting up" : "No commits scored yet"}</p>
      {active && (
        <p className="max-w-xs text-xs text-muted">
          Waiting for the first sandbox to spin up. This page updates on its own — no need to refresh.
        </p>
      )}
    </div>
  );
}

export function TimelineChart({
  timeline,
  regressionCommit,
  onSelectRegression,
  active = false,
}: {
  timeline: TimelineEntry[];
  regressionCommit: string | null;
  onSelectRegression: () => void;
  /** true while the bisector is actively running — draws a traveling
   * scan cursor along the line so an in-progress search visibly reads as
   * "in progress," not just "chart has fewer points right now." */
  active?: boolean;
}) {
  const reduceMotion = useReducedMotion();

  if (timeline.length === 0) {
    return <EmptyChart active={active} />;
  }

  const data: ChartPoint[] = timeline.map((entry) => ({
    ...entry,
    isRegression: entry.commit === regressionCommit,
  }));

  return (
    <div className="relative h-80 w-full rounded-xl border border-line bg-surface p-2">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 16, right: 12, bottom: 4, left: 0 }}>
          <defs>
            <linearGradient id="scoreArea" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--iris)" stopOpacity={0.22} />
              <stop offset="100%" stopColor="var(--iris)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis
            dataKey="commit"
            tickFormatter={(value: string) => shortSha(value, 7)}
            tick={{ fontFamily: "var(--font-data)", fontSize: 11, fill: "var(--muted)" }}
            axisLine={{ stroke: "var(--line-strong)" }}
            tickLine={false}
          />
          <YAxis
            tickFormatter={(value: number) => `${value}ms`}
            tick={{ fontFamily: "var(--font-data)", fontSize: 11, fill: "var(--muted)" }}
            axisLine={false}
            tickLine={false}
            width={52}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--line-strong)" }} />
          <Area type="monotone" dataKey="score" stroke="none" fill="url(#scoreArea)" isAnimationActive={false} />
          <Line
            type="monotone"
            dataKey="score"
            stroke="var(--iris)"
            strokeWidth={2}
            dot={makeDot(onSelectRegression)}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
      {active && !reduceMotion && (
        <motion.div
          className="pointer-events-none absolute inset-y-3 left-0 w-24 bg-gradient-to-r from-transparent via-iris/[0.08] to-transparent"
          initial={{ x: "-100%" }}
          animate={{ x: "520%" }}
          transition={{ duration: 2.6, repeat: Infinity, ease: "linear" }}
          aria-hidden
        />
      )}
    </div>
  );
}
