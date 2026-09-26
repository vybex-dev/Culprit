// FILE: frontend/src/components/dashboard/DiagnosisSection.tsx — place at this path in the Culprit repo

import type { Diagnosis } from "@/lib/types";
import { CONFIDENCE_LABEL, categoryLabel } from "@/lib/format";
import { Pill, SectionLabel, Skeleton } from "@/components/ui";
import { DiffViewer } from "./DiffViewer";

export function DiagnosisSectionLoading() {
  return (
    <div className="space-y-3">
      <SectionLabel>Diagnosis</SectionLabel>
      <Skeleton className="h-5 w-40" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}

export function DiagnosisSection({ diagnosis }: { diagnosis: Diagnosis }) {
  return (
    <div className="space-y-3">
      <SectionLabel>Diagnosis</SectionLabel>
      <div className="flex flex-wrap items-center gap-2">
        <Pill variant="iris">{categoryLabel(diagnosis.category)}</Pill>
        <span className="text-xs text-muted">{CONFIDENCE_LABEL[diagnosis.confidence]}</span>
      </div>
      <p className="text-sm leading-relaxed text-ink">{diagnosis.explanation}</p>

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

      {diagnosis.tavily_refs.length > 0 && (
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
      )}
    </div>
  );
}
