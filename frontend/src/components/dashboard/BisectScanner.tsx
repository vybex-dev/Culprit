// FILE: frontend/src/components/dashboard/BisectScanner.tsx
//
// The search, made visible. One cell per commit in the range; as the Bisector
// benchmarks commits the window of suspects visibly collapses onto the guilty
// one. It answers the question a chart of five dots can't: "of all these
// commits, which did the system actually measure, and which did it merely rule
// out by reasoning?"
//
// Solid cells are MEASUREMENTS (a sandbox ran them). Faint cells are
// INFERENCES (bisection assumes one monotonic regression). The legend says so.
// Cell logic is pure and unit-tested: lib/scanner.ts.

"use client";

import { useState } from "react";
import clsx from "clsx";
import { CopyMetaTag } from "@/components/CopyMetaTag";
import { SectionLabel } from "@/components/ui";
import { formatPctChange, formatScore, formatTimestamp, shortSha } from "@/lib/format";
import { STATE_LABEL, buildCells, scannerStats, type Cell, type CellState } from "@/lib/scanner";
import type { JobState } from "@/lib/types";

const CELL_STYLE: Record<CellState, string> = {
  baseline: "bg-iris-100 border border-iris text-iris",
  clean: "bg-resolved text-paper",
  regressed: "bg-unresolved text-paper",
  guilty: "bg-butter text-ink border border-butter-700 guilty-pulse",
  suspect: "border border-dashed border-iris/60 bg-iris-100/70 text-iris",
  "inferred-clean": "border border-resolved/25 bg-resolved/10 text-transparent",
  "inferred-regressed": "border border-unresolved/25 bg-unresolved/10 text-transparent",
};

function LegendSwatch({ state, label }: { state: CellState; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={clsx("inline-block h-3 w-3 rounded-[3px]", CELL_STYLE[state].replace("guilty-pulse", ""))} aria-hidden />
      {label}
    </span>
  );
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function CommitDetail({ cell, baseline }: { cell: Cell; baseline: number | null }) {
  const p = cell.probe;
  return (
    // min-h = the tallest normal case (a probed commit with Nano verdicts), so clicking
    // between measured / inferred / baseline cells doesn't make the card — and everything
    // under it — jump up and down.
    <div className="min-h-[11.5rem] rounded-lg border border-line bg-surface-2 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <CopyMetaTag copyText={cell.sha}>{shortSha(cell.sha, 10)}</CopyMetaTag>
        <span className="text-[11px] text-muted">{STATE_LABEL[cell.state]}</span>
        <span className="ml-auto text-[11px] text-muted">
          #{cell.index} in range{cell.date ? ` · ${formatTimestamp(cell.date)}` : ""}
        </span>
      </div>
      <p className="mt-2 font-medium text-ink">{cell.subject || "(no subject)"}</p>
      {cell.author && <p className="text-xs text-muted">{cell.author}</p>}

      {p ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-[auto_1fr]">
          <dl className="grid grid-cols-[auto_auto] gap-x-4 gap-y-1 text-xs">
            <dt className="text-muted">median</dt>
            <dd className="font-mono tabular-nums text-ink">{formatScore(p.median_score)}</dd>
            {baseline !== null && p.step !== 0 && (
              <>
                <dt className="text-muted">vs baseline</dt>
                <dd className="font-mono tabular-nums text-ink">{formatPctChange(baseline, p.median_score)}</dd>
              </>
            )}
            <dt className="text-muted">probe</dt>
            <dd className="font-mono tabular-nums text-ink">
              #{p.step}
              {p.role === "endpoint" ? " · end of range" : p.role === "baseline" ? " · baseline" : ""}
            </dd>
            {p.rounds > 1 && (
              <>
                <dt className="text-muted">rounds</dt>
                <dd className="font-mono tabular-nums text-ink" title="Nano asked for more runs because the first were inconclusive">
                  {p.rounds}
                </dd>
              </>
            )}
          </dl>
          <div>
            <div className="text-[11px] text-muted">{p.raw_scores.length} raw runs</div>
            <div className="mt-1 flex flex-wrap items-end gap-1" aria-label="Raw benchmark runs">
              {p.raw_scores.map((s, i) => {
                const top = Math.max(...p.raw_scores) || 1;
                return (
                  <span
                    key={i}
                    title={formatScore(s)}
                    className={clsx("w-2 rounded-[2px]", p.verdict === "regressed" ? "bg-unresolved/70" : p.verdict === "clean" ? "bg-resolved/70" : "bg-iris/60")}
                    style={{ height: `${Math.max(4, Math.round((s / top) * 28))}px` }}
                  />
                );
              })}
              <span className="ml-2 font-mono text-[11px] tabular-nums text-muted">
                {formatScore(Math.min(...p.raw_scores))} – {formatScore(Math.max(...p.raw_scores))}
                {p.raw_scores.length > 1 && ` (median ${formatScore(median(p.raw_scores))})`}
              </span>
            </div>
            {p.nano.length > 0 && (
              <ul className="mt-2 space-y-0.5 text-[11px] text-muted">
                {p.nano.map((n, i) => (
                  <li key={i}>
                    Nano <span className="text-ink">{n.final_verdict}</span> on {n.n_scores} runs · {Math.round(n.latency_s * 1000)}ms
                    {n.overridden && (
                      <span className="ml-1 text-butter-700" title={`Nano said "${n.nano_verdict}"; the arithmetic overrode it.`}>
                        (arithmetic override)
                      </span>
                    )}
                    {n.offline && <span className="ml-1 text-muted/70">(stand-in)</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      ) : (
        <p className="mt-2 text-xs text-muted">
          {cell.state.startsWith("inferred")
            ? "Never benchmarked — ruled out by bisection's single-regression assumption, not by a measurement."
            : "Not benchmarked yet."}
        </p>
      )}
    </div>
  );
}

export function BisectScanner({ job, className }: { job: JobState; className?: string }) {
  const commits = job.commits ?? [];
  const probes = job.probes ?? [];
  const stopped = job.status === "failed" || job.status === "cancelled";
  const done = job.status === "done";
  const [picked, setPicked] = useState<number | null>(null);

  if (commits.length === 0) return null;

  const cells = buildCells(commits, probes, job.window, job.regression_commit, done && job.regression_commit === null);
  const stats = scannerStats(commits, probes, job.regression_commit, done);
  const guilty = cells.find((c) => c.state === "guilty") ?? null;
  const baseline = job.baseline_score ?? probes.find((p) => p.step === 0)?.median_score ?? null;
  const selected = cells[picked ?? guilty?.index ?? -1] ?? null;
  const showNumbers = commits.length <= 48;
  const n = commits.length;
  const [lo, hi] = job.window ?? [1, n - 1];
  const windowVisible = !done && !stopped && hi >= lo && job.status === "bisecting" && probes.length > 1;
  const sorted = [...probes].sort((a, b) => a.step - b.step);

  return (
    <section className={clsx("space-y-3", className)} aria-label="Bisect scanner">
      <SectionLabel>Bisect scanner — one cell per commit</SectionLabel>

      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <p className="text-sm text-ink">
          <span className="font-mono tabular-nums">{stats.probesUsed}</span>{" "}
          {stats.probesUsed === 1 ? "commit" : "commits"} benchmarked
          {stats.converged ? (
            <>
              {" "}
              out of <span className="font-mono tabular-nums">{stats.linearRuns}</span>
              {stats.speedup !== null && (
                <>
                  {" "}
                  — <span className="font-medium">{stats.speedup.toFixed(1)}× fewer runs</span> than a linear scan
                </>
              )}
            </>
          ) : (
            <span className="text-muted"> so far — a linear scan would need {stats.linearRuns}</span>
          )}
        </p>
        {!stats.converged && !stopped && <p className="text-xs text-muted">narrowing the window…</p>}
        {!stats.converged && stopped && (
          <p className="text-xs text-muted">
            {job.status === "cancelled" ? "cancelled" : "stopped"} before the search finished — unmeasured commits stay suspects
          </p>
        )}
      </div>

      <div className="relative pb-11">
        <div
          className="grid gap-[3px]"
          style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}
          role="group"
          aria-label="Commits in the searched range, oldest to newest"
        >
          {cells.map((c) => {
            const isSel = selected?.index === c.index;
            return (
              <button
                key={c.sha}
                type="button"
                onClick={() => setPicked(c.index)}
                aria-pressed={isSel}
                aria-label={`${shortSha(c.sha)} ${c.subject} — ${STATE_LABEL[c.state]}`}
                title={`${shortSha(c.sha)} · ${c.subject}\n${STATE_LABEL[c.state]}${c.probe ? ` · ${formatScore(c.probe.median_score)}` : ""}`}
                className={clsx(
                  "relative flex h-11 items-center justify-center rounded-[4px] font-mono text-[10px] font-medium transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5",
                  CELL_STYLE[c.state],
                  isSel && "outline outline-2 outline-offset-2 outline-ink/70",
                )}
              >
                {showNumbers && c.probe && c.probe.step > 0 ? c.probe.step : c.state === "baseline" ? "0" : ""}
              </button>
            );
          })}
        </div>

        {windowVisible && (
          <div className="pointer-events-none absolute inset-x-0 top-[3.3rem] h-8" aria-hidden>
            <div
              className="absolute top-0 h-2.5 rounded-b border-x border-b border-iris/70 transition-[left,width] duration-500 ease-out"
              style={{ left: `${(lo / n) * 100}%`, width: `${((hi - lo + 1) / n) * 100}%` }}
            />
            <span
              className="absolute top-3 whitespace-nowrap text-[10px] text-iris transition-[left] duration-500"
              style={{ left: `${(lo / n) * 100}%` }}
            >
              {hi - lo + 1} suspect{hi - lo + 1 === 1 ? "" : "s"}
            </span>
          </div>
        )}
        <div className="absolute inset-x-0 bottom-0 flex justify-between font-mono text-[10px] text-muted/70" aria-hidden>
          <span>{shortSha(commits[0].sha)} · known good</span>
          <span>{shortSha(commits[n - 1].sha)} · HEAD</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted">
        <LegendSwatch state="clean" label="measured clean" />
        <LegendSwatch state="regressed" label="measured regressed" />
        <LegendSwatch state="guilty" label="guilty" />
        <LegendSwatch state="suspect" label="suspect" />
        <LegendSwatch state="inferred-clean" label="inferred" />
        <span className="text-muted/70">solid = measured in a sandbox · faint = ruled out by reasoning, never run</span>
      </div>

      {selected && <CommitDetail cell={selected} baseline={baseline} />}

      {sorted.length > 1 && (
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-left text-xs">
            <caption className="sr-only">Benchmarked commits in the order they were probed</caption>
            <thead className="bg-surface-2 text-[11px] text-muted">
              <tr>
                <th className="px-3 py-1.5 font-normal">#</th>
                <th className="px-3 py-1.5 font-normal">commit</th>
                <th className="hidden px-3 py-1.5 font-normal sm:table-cell">subject</th>
                <th className="px-3 py-1.5 text-right font-normal">median</th>
                <th className="px-3 py-1.5 text-right font-normal">Δ baseline</th>
                <th className="px-3 py-1.5 font-normal">verdict</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => {
                const subject = commits.find((c) => c.sha === p.commit)?.subject ?? "";
                const isGuilty = p.commit === job.regression_commit;
                return (
                  <tr
                    key={p.step}
                    onClick={() => setPicked(p.index)}
                    className={clsx("cursor-pointer border-t border-line transition-colors hover:bg-ink/[0.03]", isGuilty && "bg-butter/15")}
                  >
                    <td className="px-3 py-1.5 font-mono tabular-nums text-muted">{p.step}</td>
                    <td className="px-3 py-1.5 font-mono text-ink">{shortSha(p.commit)}</td>
                    <td className="hidden max-w-[28ch] truncate px-3 py-1.5 text-muted sm:table-cell">{subject}</td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{formatScore(p.median_score)}</td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums text-muted">
                      {p.step === 0 || baseline === null ? "—" : formatPctChange(baseline, p.median_score)}
                    </td>
                    <td className="px-3 py-1.5">
                      <span
                        className={clsx(
                          "rounded-full px-2 py-0.5 text-[11px]",
                          p.verdict === "regressed" ? "bg-unresolved/15 text-unresolved" : p.verdict === "clean" ? "bg-resolved/15 text-resolved" : "bg-iris-100 text-iris",
                        )}
                      >
                        {p.verdict}
                        {isGuilty ? " · guilty" : ""}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
