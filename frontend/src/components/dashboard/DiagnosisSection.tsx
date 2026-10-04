// FILE: frontend/src/components/dashboard/DiagnosisSection.tsx

import { motion, useReducedMotion } from "motion/react";
import clsx from "clsx";
import type { Confidence, Diagnosis } from "@/lib/types";
import { formatScore } from "@/lib/format";
import { CONFIDENCE_LABEL, categoryLabel } from "@/lib/format";
import { Pill, SectionLabel, Skeleton } from "@/components/ui";
import { DiffViewer } from "./DiffViewer";

export function DiagnosisSectionLoading() {
  return (
    <div className="space-y-3">
      <SectionLabel>Diagnosis</SectionLabel>
      <div className="flex items-center gap-2 text-xs text-muted">
        <span className="breathe-dot h-1.5 w-1.5 rounded-full bg-iris" aria-hidden />
        Nemotron 3 Ultra is analyzing the guilty diff…
      </div>
      <Skeleton className="h-5 w-40" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}

/** One orchestrated reveal, staggered top to bottom, the moment the
 * diagnosis first arrives — the panel's single deliberate motion moment.
 * Subsequent re-renders of the same diagnosis don't replay it (key'd by
 * category+explanation upstream via React reconciling the same
 * elements), and reduced-motion users see it appear instantly. */
function Reveal({ index, children }: { index: number; children: React.ReactNode }) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, delay: reduceMotion ? 0 : index * 0.07, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  );
}

const CONFIDENCE_LEVEL: Record<Confidence, number> = { low: 1, medium: 2, high: 3 };

function ConfidenceMeter({ confidence }: { confidence: Confidence }) {
  const level = CONFIDENCE_LEVEL[confidence] ?? 1;
  return (
    <span className="inline-flex items-center gap-2 text-xs text-muted" title={CONFIDENCE_LABEL[confidence]}>
      <span className="flex items-center gap-[3px]" aria-hidden>
        {[1, 2, 3].map((i) => (
          <span key={i} className={clsx("h-3 w-1.5 rounded-sm", i <= level ? "bg-iris" : "bg-ink/15")} />
        ))}
      </span>
      {CONFIDENCE_LABEL[confidence]}
    </span>
  );
}

function Stat({ label, value, tone = "default" }: { label: string; value: string; tone?: "default" | "bad" }) {
  return (
    <div className="min-w-0 flex-1 rounded-xl border border-line bg-surface px-3.5 py-3">
      <div className="text-[11px] text-muted">{label}</div>
      <div
        className={clsx(
          "mt-1 truncate font-mono text-[17px] font-medium tabular-nums",
          tone === "bad" ? "text-unresolved" : "text-ink",
        )}
      >
        {value}
      </div>
    </div>
  );
}

export interface RegressionImpact {
  before: number;
  after: number;
}

export function DiagnosisSection({ diagnosis, impact }: { diagnosis: Diagnosis; impact?: RegressionImpact | null }) {
  const paragraphs = diagnosis.explanation.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
  const factor = impact && impact.before > 0 ? impact.after / impact.before : null;

  return (
    <div className="space-y-6">
      <Reveal index={0}>
        <div className="overflow-hidden rounded-2xl border border-line bg-surface-2">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line px-5 py-3.5">
            <div className="flex items-center gap-2.5">
              <span className="text-[13px] text-muted">Root cause</span>
              <Pill variant="iris">{categoryLabel(diagnosis.category)}</Pill>
            </div>
            <ConfidenceMeter confidence={diagnosis.confidence} />
          </div>
          <div className="space-y-3 px-5 py-5">
            {paragraphs.map((text, i) => (
              <p key={i} className="text-[15px] leading-7 text-ink">
                {text}
              </p>
            ))}
          </div>
          {impact && (
            <div className="flex flex-col gap-2.5 border-t border-line px-5 py-4 sm:flex-row">
              <Stat label="Baseline (known good)" value={formatScore(impact.before)} />
              <Stat label="At this commit" value={formatScore(impact.after)} tone="bad" />
              {factor !== null && (
                <Stat label="Slowdown" value={factor >= 10 ? `${Math.round(factor)}×` : `${factor.toFixed(1)}×`} tone="bad" />
              )}
            </div>
          )}
        </div>
      </Reveal>

      <Reveal index={1}>
        <div className="space-y-3">
          <SectionLabel>Evidence</SectionLabel>
          {diagnosis.diff ? (
            <div className="space-y-2">
              <p className="flex items-center gap-2 text-xs text-muted">
                <span
                  className="h-3.5 w-2.5 rounded-sm border-l-2 border-[var(--cite-bar)] bg-[image:linear-gradient(var(--cite-overlay),var(--cite-overlay))] ring-1 ring-line-strong"
                  aria-hidden
                />
                Lines the diagnosis points to are highlighted — added/removed lines keep their own color underneath.
              </p>
              <DiffViewer diff={diagnosis.diff} citedLines={diagnosis.cited_lines} />
            </div>
          ) : (
            diagnosis.cited_lines.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs text-muted">
                  Cited lines (full diff not available from the API for this job — see hand-back notes):
                </p>
                <div className="space-y-1 rounded-xl bg-[var(--console-bg)] p-3 font-mono text-[12.5px]">
                  {diagnosis.cited_lines.map((line, i) => (
                    <div key={i} className="rounded bg-[var(--cite-bg)] px-2 py-0.5 text-[var(--cite-text)]">
                      {line}
                    </div>
                  ))}
                </div>
              </div>
            )
          )}
        </div>
      </Reveal>

      {diagnosis.tavily_refs.length > 0 && (
        <Reveal index={2}>
          <div className="space-y-3">
            <SectionLabel>Grounded against</SectionLabel>
            <ul className="grid gap-2 sm:grid-cols-2">
              {diagnosis.tavily_refs.map((ref) => (
                <li key={ref.url}>
                  <a
                    href={ref.url}
                    target="_blank"
                    rel="noreferrer"
                    className="group flex h-full items-start justify-between gap-2 rounded-xl border border-line bg-surface px-3.5 py-3 text-sm text-ink transition-colors hover:border-iris/50"
                  >
                    <span className="min-w-0 leading-snug">{ref.title}</span>
                    <span className="mt-0.5 text-muted transition-colors group-hover:text-iris" aria-hidden>
                      ↗
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </Reveal>
      )}
    </div>
  );
}
