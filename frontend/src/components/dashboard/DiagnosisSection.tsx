// FILE: frontend/src/components/dashboard/DiagnosisSection.tsx — place at this path in the Culprit repo

import { motion, useReducedMotion } from "motion/react";
import type { Diagnosis } from "@/lib/types";
import { CONFIDENCE_LABEL, categoryLabel } from "@/lib/format";
import { Pill, SectionLabel, Skeleton } from "@/components/ui";
import { DiffViewer } from "./DiffViewer";

export function DiagnosisSectionLoading() {
  return (
    <div className="space-y-3">
      <SectionLabel>Diagnosis</SectionLabel>
      <div className="flex items-center gap-2 text-xs text-muted">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-iris" aria-hidden />
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
 * diagnosis first arrives — the panel's single deliberate motion moment
 * (frontend-design skill: one orchestrated sequence, not scattered
 * per-element fades). Subsequent re-renders of the same diagnosis don't
 * replay it (key'd by category+explanation upstream via React reconciling
 * the same elements), and reduced-motion users see it appear instantly. */
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

export function DiagnosisSection({ diagnosis }: { diagnosis: Diagnosis }) {
  return (
    <div className="space-y-3">
      <Reveal index={0}>
        <SectionLabel>Diagnosis</SectionLabel>
      </Reveal>
      <Reveal index={1}>
        <div className="flex flex-wrap items-center gap-2">
          <Pill variant="iris">{categoryLabel(diagnosis.category)}</Pill>
          <span className="text-xs text-muted">{CONFIDENCE_LABEL[diagnosis.confidence]}</span>
        </div>
      </Reveal>
      <Reveal index={2}>
        <p className="text-sm leading-relaxed text-ink">{diagnosis.explanation}</p>
      </Reveal>

      <Reveal index={3}>
        {diagnosis.diff ? (
          <div>
            <p className="mb-1.5 text-xs text-muted">Cited lines are highlighted below.</p>
            <DiffViewer diff={diagnosis.diff} citedLines={diagnosis.cited_lines} />
          </div>
        ) : (
          diagnosis.cited_lines.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs text-muted">
                Cited lines (full diff not available from the API for this job — see hand-back notes):
              </p>
              <div className="space-y-1 rounded-md bg-[var(--console-bg)] p-3 font-mono text-[12.5px]">
                {diagnosis.cited_lines.map((line, i) => (
                  <div key={i} className="rounded bg-[var(--cite-bg)] px-2 py-0.5 text-[var(--cite-text)]">
                    {line}
                  </div>
                ))}
              </div>
            </div>
          )
        )}
      </Reveal>

      {diagnosis.tavily_refs.length > 0 && (
        <Reveal index={4}>
          <div className="space-y-1.5 pt-1">
            <p className="text-xs text-muted">Grounded against</p>
            <ul className="space-y-1">
              {diagnosis.tavily_refs.map((ref) => (
                <li key={ref.url}>
                  <a
                    href={ref.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-sm text-iris underline decoration-iris/30 underline-offset-2 hover:decoration-iris"
                  >
                    {ref.title}
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
