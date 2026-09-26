// FILE: frontend/src/components/dashboard/TimelineChart.tsx — place at this path in the Culprit repo

"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
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
    <div className="rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-sm">
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
        <motion.circle
          cx={cx}
          cy={cy}
          r={9}
          fill="var(--butter)"
          initial={{ opacity: 0.35 }}
          animate={{ r: [9, 15, 9], opacity: [0.35, 0.05, 0.35] }}
          transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
        />
        <circle cx={cx} cy={cy} r={5.5} fill="var(--butter)" stroke="var(--iris-700)" strokeWidth={1.5} />
      </g>
    );
  }
  return Dot;
}

function EmptyChart({ active }: { active: boolean }) {
  const reduceMotion = useReducedMotion();
  return (
    <div className="flex h-72 flex-col items-center justify-center gap-2.5 rounded-md border border-dashed border-line-strong text-center">
      <div className="relative h-2 w-2" aria-hidden>
        <span className="absolute inset-0 rounded-full bg-iris" />
        {active && !reduceMotion && (
          <motion.span
            className="absolute inset-0 rounded-full bg-iris"
            initial={{ opacity: 0.6, scale: 1 }}
            animate={{ opacity: 0, scale: 3.2 }}
            transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut" }}
          />
        )}
      </div>
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
    <div className="relative h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 18, right: 12, bottom: 4, left: 0 }}>
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
          <Line
            type="monotone"
            dataKey="score"
            stroke="var(--iris)"
            strokeWidth={2}
            dot={makeDot(onSelectRegression)}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
      {active && !reduceMotion && (
        <motion.div
          className="pointer-events-none absolute inset-y-3 left-0 w-24 bg-gradient-to-r from-transparent via-iris/[0.06] to-transparent"
          initial={{ x: "-100%" }}
          animate={{ x: "520%" }}
          transition={{ duration: 2.6, repeat: Infinity, ease: "linear" }}
          aria-hidden
        />
      )}
    </div>
  );
}
