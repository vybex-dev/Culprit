// FILE: frontend/src/components/dashboard/TimelineChart.tsx
//
// The evidence chart. Unlike a plain line of medians it shows:
//   * the real BASELINE and the regression THRESHOLD band, so "regressed" is a
//     visible line being crossed, not a label;
//   * every RAW RUN behind each median, so noise is visible, not hidden;
//   * commits placed by their position in history (unmeasured gaps are real
//     gaps — a straight line between two probes is not a measurement);
//   * the VERIFIED FIX landing back inside the band, when there is one.
//
// Custom SVG (no chart library): the overlays above don't map cleanly onto a
// generic line chart, and this keeps the bundle lean. Falls back gracefully to
// the coarse `timeline` for jobs that predate per-probe data.

"use client";

import { useEffect, useMemo, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import clsx from "clsx";
import type { Commit, Probe, TimelineEntry } from "@/lib/types";
import { formatPctChange, formatScore, formatTimestamp, shortSha } from "@/lib/format";

interface Pt {
  key: string;
  sha: string;
  index: number; // position in history (or in the timeline when there's no commit list)
  median: number;
  runs: number[];
  verdict: string;
  step: number | null;
  subject: string;
  timestamp: string;
  isRegression: boolean;
}

const H = 320;
const M = { top: 30, right: 22, bottom: 30, left: 58 };

/** Tracks an element's width. Returns a CALLBACK ref (not a ref object): the
 * chart sits behind an early "empty" return, so the element may not exist at
 * mount — a callback ref re-attaches the observer whenever it does appear. */
function useWidth(): [(el: HTMLDivElement | null) => void, number] {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const [w, setW] = useState(640);
  useEffect(() => {
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setW(Math.max(280, Math.round(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, w];
}

/** Round gridline values from 0 up to — and always including — one tick at or
 * above `max`, so the axis can never end below the data it's plotting. */
function niceTicks(max: number, count = 4): number[] {
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out = [0];
  let v = 0;
  while (v < max) {
    v += step;
    out.push(Number(v.toFixed(6)));
  }
  return out;
}

function fmtTick(v: number): string {
  if (v >= 1000) return `${(v / 1000).toFixed(v % 1000 === 0 ? 0 : 1)}s`;
  return `${Number.isInteger(v) ? v : v.toFixed(1)}`;
}

function EmptyChart({ active }: { active: boolean }) {
  return (
    <div className="flex h-80 flex-col items-center justify-center gap-2.5 rounded-xl border border-dashed border-line-strong text-center">
      <span className="relative flex h-2 w-2" aria-hidden>
        {active && <span className="breathe-ring absolute inset-0 rounded-full bg-iris" />}
        <span className="relative h-2 w-2 rounded-full bg-iris" />
      </span>
      <p className="text-sm text-ink">{active ? "Starting up" : "No commits scored yet"}</p>
      {active && (
        <p className="max-w-xs text-xs text-muted">
          Waiting for the first sandbox to report. This page updates on its own — no need to refresh.
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
  commits,
  probes,
  baseline,
  thresholdPct,
  fix,
  className,
}: {
  timeline: TimelineEntry[];
  regressionCommit: string | null;
  onSelectRegression: () => void;
  /** true while the bisector is actively running — draws a traveling scan
   * cursor so an in-progress search reads as "in progress". */
  active?: boolean;
  commits?: Commit[];
  probes?: Probe[];
  baseline?: number | null;
  thresholdPct?: number | null;
  /** The proposed fix's measured score. `verified` only ever mirrors a real re-run. */
  fix?: { afterScore: number; verified: boolean } | null;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  const [wrapRef, width] = useWidth();
  const [hover, setHover] = useState<string | null>(null);
  const [logScale, setLogScale] = useState(false);

  const points: Pt[] = useMemo(() => {
    const subjectOf = new Map((commits ?? []).map((c) => [c.sha, c.subject]));
    const indexOf = new Map((commits ?? []).map((c) => [c.sha, c.index]));
    if (probes && probes.length > 0) {
      return [...probes]
        .sort((a, b) => a.index - b.index)
        .map((p) => ({
          key: p.commit,
          sha: p.commit,
          index: p.index,
          median: p.median_score,
          runs: p.raw_scores,
          verdict: p.commit === regressionCommit ? "regressed" : p.verdict,
          step: p.step,
          subject: subjectOf.get(p.commit) ?? "",
          timestamp: p.timestamp,
          isRegression: p.commit === regressionCommit,
        }));
    }
    // Legacy: only the coarse timeline exists.
    return timeline.map((e, i) => ({
      key: e.commit,
      sha: e.commit,
      index: indexOf.get(e.commit) ?? i,
      median: e.score,
      runs: [],
      verdict: e.commit === regressionCommit ? "regressed" : "clean",
      step: null,
      subject: subjectOf.get(e.commit) ?? "",
      timestamp: e.timestamp,
      isRegression: e.commit === regressionCommit,
    }));
  }, [probes, timeline, commits, regressionCommit]);

  if (points.length === 0) return <EmptyChart active={active} />;

  const base = baseline ?? points.find((p) => p.step === 0)?.median ?? points[0].median;
  const limit = thresholdPct != null && base ? base * (1 + thresholdPct / 100) : null;
  const n = commits && commits.length > 1 ? commits.length : Math.max(points.length, 2);
  const maxIndex = commits && commits.length > 1 ? commits.length - 1 : Math.max(points.length - 1, 1);

  const yValues = [...points.flatMap((p) => [p.median, ...p.runs]), base, limit ?? 0, fix?.afterScore ?? 0];
  const dataMax = Math.max(...yValues);
  const linearTicks = niceTicks(dataMax * 1.04);
  // Snap the axis to its top tick so no gridline/label ever sits outside the plot.
  const yMax = logScale ? dataMax * 1.12 : linearTicks[linearTicks.length - 1];
  const yMinLog = Math.max(Math.min(...yValues.filter((v) => v > 0)) * 0.7, 0.01);

  const innerW = width - M.left - M.right;
  const innerH = H - M.top - M.bottom;
  const x = (idx: number) => M.left + (maxIndex === 0 ? innerW / 2 : (idx / maxIndex) * innerW);
  const y = (v: number) => {
    if (logScale) {
      const t = (Math.log10(Math.max(v, yMinLog)) - Math.log10(yMinLog)) / (Math.log10(yMax) - Math.log10(yMinLog));
      return M.top + innerH - t * innerH;
    }
    return M.top + innerH - (v / yMax) * innerH;
  };
  const ticks = logScale ? [yMinLog, base, dataMax].map((v) => Number(v.toPrecision(2))) : linearTicks;

  const guilty = points.find((p) => p.isRegression) ?? null;
  const hovered = points.find((p) => p.key === hover) ?? null;
  const showLabels = (p: Pt) => p.step === 0 || p.isRegression || p.key === points[points.length - 1].key || p.key === hover;

  return (
    <div className={clsx("relative w-full rounded-xl border border-line bg-surface", className)}>
      <div className="flex items-center justify-between px-4 pt-3 text-[11px] text-muted">
        <span>
          Benchmark median per commit · <span className="text-ink/70">dots are the raw runs</span>
        </span>
        <button
          type="button"
          onClick={() => setLogScale((v) => !v)}
          aria-pressed={logScale}
          className="rounded border border-line px-2 py-0.5 hover:border-line-strong hover:text-ink"
          title="Log scale makes a 30× regression and the noise around the baseline readable at once"
        >
          {logScale ? "log scale" : "linear scale"}
        </button>
      </div>

      <div ref={wrapRef} className="relative">
        <svg width={width} height={H} role="img" aria-label="Benchmark score per benchmarked commit" className="block overflow-visible">
          {/* grid + y labels */}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} stroke="var(--line)" />
              <text x={M.left - 8} y={y(t) + 3.5} textAnchor="end" fontSize={11} fontFamily="var(--font-data)" fill="var(--muted)">
                {fmtTick(t)}
              </text>
            </g>
          ))}
          <text x={M.left - 8} y={M.top - 12} textAnchor="end" fontSize={10} fontFamily="var(--font-data)" fill="var(--muted)">
            ms
          </text>

          {/* acceptable band: baseline … baseline × (1 + threshold) */}
          {limit !== null && (
            <g>
              <rect x={M.left} width={innerW} y={y(limit)} height={Math.max(1, y(base) - y(limit))} fill="var(--resolved)" opacity={0.1} />
              <line x1={M.left} x2={width - M.right} y1={y(limit)} y2={y(limit)} stroke="var(--resolved)" strokeDasharray="4 4" opacity={0.7} />
              <text x={width - M.right - 4} y={y(limit) - 5} textAnchor="end" fontSize={10} fill="var(--resolved)" paintOrder="stroke" stroke="var(--surface)" strokeWidth={4} strokeLinejoin="round">
                regression threshold +{thresholdPct}%
              </text>
            </g>
          )}
          <line x1={M.left} x2={width - M.right} y1={y(base)} y2={y(base)} stroke="var(--iris)" strokeDasharray="2 4" opacity={0.65} />
          <text x={M.left + 4} y={y(base) + 14} fontSize={10} fill="var(--iris)" paintOrder="stroke" stroke="var(--surface)" strokeWidth={4} strokeLinejoin="round">
            baseline {formatScore(base)}
          </text>

          {/* median line — solid only between ADJACENT commits that were both measured;
              across a gap of unmeasured commits it's dashed, because the slope there is not data */}
          {points.slice(1).map((b, i) => {
            const a = points[i];
            const gap = b.index - a.index > 1;
            return (
              <line
                key={`seg-${a.key}-${b.key}`}
                x1={x(a.index)}
                y1={y(a.median)}
                x2={x(b.index)}
                y2={y(b.median)}
                stroke="var(--iris)"
                strokeWidth={gap ? 1.5 : 2}
                strokeDasharray={gap ? "4 4" : undefined}
                opacity={gap ? 0.55 : 0.85}
              />
            );
          })}

          {/* fix: guilty → verified score */}
          {fix && guilty && (
            <g>
              <line
                x1={x(guilty.index)}
                x2={x(guilty.index)}
                y1={y(guilty.median)}
                y2={y(fix.afterScore)}
                stroke={fix.verified ? "var(--resolved)" : "var(--muted)"}
                strokeDasharray="3 3"
              />
              <rect
                x={x(guilty.index) - 6}
                y={y(fix.afterScore) - 6}
                width={12}
                height={12}
                transform={`rotate(45 ${x(guilty.index)} ${y(fix.afterScore)})`}
                fill={fix.verified ? "var(--resolved)" : "var(--surface)"}
                stroke={fix.verified ? "var(--resolved)" : "var(--muted)"}
                strokeWidth={1.5}
              />
              <text
                x={x(guilty.index) + 14}
                y={y(fix.afterScore) - 10}
                fontSize={11}
                fontWeight={500}
                fill={fix.verified ? "var(--resolved)" : "var(--muted)"}
                paintOrder="stroke" stroke="var(--surface)" strokeWidth={4} strokeLinejoin="round"
              >
                {fix.verified ? "fix verified" : "fix not verified"} · {formatScore(fix.afterScore)}
              </text>
            </g>
          )}

          {/* raw runs */}
          {points.map((p) =>
            p.runs.map((r, i) => (
              <circle
                key={`${p.key}-${i}`}
                cx={x(p.index) + (i - (p.runs.length - 1) / 2) * 2.6}
                cy={y(r)}
                r={1.8}
                fill={p.verdict === "regressed" ? "var(--unresolved)" : "var(--iris)"}
                opacity={0.4}
              />
            )),
          )}

          {/* probes */}
          {points.map((p) => {
            const cx = x(p.index);
            const cy = y(p.median);
            if (p.isRegression) {
              return (
                <g
                  key={p.key}
                  role="button"
                  tabIndex={0}
                  style={{ cursor: "pointer" }}
                  aria-label={`Open diagnosis for commit ${shortSha(p.sha)}`}
                  onClick={onSelectRegression}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") onSelectRegression();
                  }}
                >
                  {/* pulse via scale transform (not the SVG `r` attribute — Motion can
                      momentarily hand the DOM an undefined r mid-tween) */}
                  <motion.circle
                    cx={cx}
                    cy={cy}
                    r={9}
                    fill="var(--butter)"
                    style={{ transformOrigin: `${cx}px ${cy}px` }}
                    initial={{ scale: 1, opacity: 0.35 }}
                    animate={reduceMotion ? { opacity: 0.25 } : { scale: [1, 16 / 9, 1], opacity: [0.35, 0.05, 0.35] }}
                    transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
                  />
                  <circle cx={cx} cy={cy} r={6} fill="var(--butter)" stroke="var(--iris-700)" strokeWidth={1.5} />
                </g>
              );
            }
            return (
              <circle
                key={p.key}
                cx={cx}
                cy={cy}
                r={p.step === 0 ? 4.5 : 3.8}
                fill={p.step === 0 ? "var(--surface)" : p.verdict === "regressed" ? "var(--unresolved)" : "var(--resolved)"}
                stroke={p.step === 0 ? "var(--iris)" : "var(--surface)"}
                strokeWidth={p.step === 0 ? 2 : 1.5}
              />
            );
          })}

          {/* x labels for the landmarks */}
          {points.filter(showLabels).map((p) => (
            <text
              key={`l-${p.key}`}
              x={x(p.index)}
              y={H - 10}
              textAnchor="middle"
              fontSize={10}
              fontFamily="var(--font-data)"
              fill={p.isRegression ? "var(--ink)" : "var(--muted)"}
              fontWeight={p.isRegression ? 600 : 400}
            >
              {shortSha(p.sha)}
            </text>
          ))}

          {/* hover targets: a full-height column per probe */}
          {points.map((p) => (
            <rect
              key={`h-${p.key}`}
              x={x(p.index) - Math.max(10, innerW / maxIndex / 2)}
              width={Math.max(20, innerW / maxIndex)}
              y={M.top}
              height={innerH}
              fill="transparent"
              onPointerEnter={() => setHover(p.key)}
              onPointerLeave={() => setHover((h) => (h === p.key ? null : h))}
            />
          ))}
          {hovered && <line x1={x(hovered.index)} x2={x(hovered.index)} y1={M.top} y2={M.top + innerH} stroke="var(--line-strong)" />}
        </svg>

        {hovered && (
          <div
            className="pointer-events-none absolute z-10 w-56 rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-lg"
            style={{ left: Math.min(Math.max(x(hovered.index) - 112, 4), width - 228), top: 8 }}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-mono text-ink">{shortSha(hovered.sha)}</span>
              {hovered.step !== null && <span className="text-muted">probe {hovered.step}</span>}
            </div>
            {hovered.subject && <div className="mt-0.5 truncate text-muted">{hovered.subject}</div>}
            <div className="mt-1 font-mono font-medium text-ink">
              {formatScore(hovered.median)}
              {hovered.step !== 0 && <span className="ml-2 font-normal text-muted">{formatPctChange(base, hovered.median)}</span>}
            </div>
            {hovered.runs.length > 0 && (
              <div className="mt-0.5 text-muted">
                {hovered.runs.length} runs · {formatScore(Math.min(...hovered.runs))}–{formatScore(Math.max(...hovered.runs))}
              </div>
            )}
            {hovered.timestamp && <div className="mt-0.5 text-muted/70">{formatTimestamp(hovered.timestamp)}</div>}
            {hovered.isRegression && <div className="mt-1 text-iris-700">Guilty commit — click to open</div>}
          </div>
        )}

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
      <p className="px-4 pb-3 text-[11px] text-muted/80">
        {n > points.length ? `${n - points.length} of ${n} commits were never run — the search ruled them out. ` : ""}
        Dashed segments cross commits that were never run — the slope there is not a measurement.
      </p>
    </div>
  );
}
