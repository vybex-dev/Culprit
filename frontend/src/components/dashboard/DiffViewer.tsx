// FILE: frontend/src/components/dashboard/DiffViewer.tsx — place at this path in the Culprit repo

import clsx from "clsx";
import { isCitedLine, parseUnifiedDiff, type DiffLine } from "@/lib/diff";

function LineRow({ line, cited }: { line: DiffLine; cited: boolean }) {
  if (line.kind === "hunk") {
    return (
      <div className="px-3 py-1 text-[var(--console-muted)] select-none">{line.content || "@@"}</div>
    );
  }
  if (line.kind === "meta") {
    return <div className="px-3 py-0.5 text-[var(--console-muted)]">{line.content}</div>;
  }

  const marker = line.kind === "add" ? "+" : line.kind === "del" ? "-" : " ";

  return (
    <div
      className={clsx(
        "grid grid-cols-[3ch_3ch_1.5ch_1fr] gap-2 px-3 py-0.5 leading-5",
        cited && "bg-[var(--cite-bg)] text-[var(--cite-text)]",
        !cited && line.kind === "add" && "bg-[var(--diff-add-bg)] text-[var(--diff-add-text)]",
        !cited && line.kind === "del" && "bg-[var(--diff-del-bg)] text-[var(--diff-del-text)]",
        !cited && line.kind === "context" && "text-[var(--console-text)]",
      )}
    >
      <span className="text-right text-[var(--console-muted)]">{line.oldLineNo ?? ""}</span>
      <span className="text-right text-[var(--console-muted)]">{line.newLineNo ?? ""}</span>
      <span className="select-none opacity-70">{marker}</span>
      <span className="whitespace-pre-wrap break-words">{line.content || " "}</span>
    </div>
  );
}

export function DiffViewer({
  diff,
  citedLines = [],
  className,
}: {
  diff: string;
  citedLines?: string[];
  className?: string;
}) {
  const hunks = parseUnifiedDiff(diff);

  if (hunks.length === 0) {
    return (
      <pre className={clsx("rounded-md bg-[var(--console-bg)] p-3 font-mono text-xs text-[var(--console-muted)]", className)}>
        (empty diff)
      </pre>
    );
  }

  return (
    <div className={clsx("overflow-x-auto rounded-md bg-[var(--console-bg)] font-mono text-[12.5px]", className)}>
      {hunks.map((hunk, hunkIndex) => (
        <div key={hunkIndex} className={hunkIndex > 0 ? "mt-2 border-t border-white/10 pt-2" : ""}>
          {hunk.header && <LineRow line={{ kind: "hunk", content: hunk.header, oldLineNo: null, newLineNo: null }} cited={false} />}
          {hunk.lines.map((line, lineIndex) => (
            <LineRow key={lineIndex} line={line} cited={isCitedLine(line, citedLines)} />
          ))}
        </div>
      ))}
    </div>
  );
}
