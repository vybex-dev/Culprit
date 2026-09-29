// FILE: frontend/src/components/dashboard/DiffViewer.tsx

"use client";

import { useState } from "react";
import clsx from "clsx";
import { isCitedLine, parseUnifiedDiff, type DiffLine } from "@/lib/diff";

function LineRow({ line, cited }: { line: DiffLine; cited: boolean }) {
  if (line.kind === "hunk") {
    return <div className="px-3 py-1 text-[var(--console-muted)] select-none">{line.content || "@@"}</div>;
  }
  if (line.kind === "meta") {
    return <div className="px-3 py-0.5 text-[var(--console-muted)]">{line.content}</div>;
  }

  const marker = line.kind === "add" ? "+" : line.kind === "del" ? "-" : " ";

  return (
    <div
      className={clsx(
        "grid grid-cols-[3ch_3ch_1.5ch_1fr] gap-2 border-l-2 px-3 py-0.5 leading-5",
        cited ? "border-l-[var(--cite-bar)]" : "border-l-transparent",
        // Base color always comes from the line's own kind — cited never
        // hides whether a line was added, removed, or unchanged.
        line.kind === "add" && "bg-[var(--diff-add-bg)] text-[var(--diff-add-text)]",
        line.kind === "del" && "bg-[var(--diff-del-bg)] text-[var(--diff-del-text)]",
        line.kind === "context" && "text-[var(--console-text)]",
        // Cited adds a translucent yellow wash on top of whatever's
        // already there (a separate CSS property from bg-color, so it
        // layers instead of overriding the add/del fill).
        cited && "bg-[image:linear-gradient(var(--cite-overlay),var(--cite-overlay))]",
      )}
    >
      <span className="text-right text-[var(--console-muted)]">{line.oldLineNo ?? ""}</span>
      <span className="text-right text-[var(--console-muted)]">{line.newLineNo ?? ""}</span>
      <span className="select-none opacity-70">{marker}</span>
      <span className="whitespace-pre-wrap break-words">{line.content || " "}</span>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // Clipboard access can be denied by the browser — fail silently,
      // this is a convenience affordance, not a required action.
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="rounded px-1.5 py-0.5 font-mono text-[10px] text-[var(--console-muted)] transition-colors hover:bg-white/10 hover:text-[var(--console-text)]"
    >
      {copied ? "copied" : "copy"}
    </button>
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
      <pre className={clsx("rounded-xl bg-[var(--console-bg)] p-3 font-mono text-xs text-[var(--console-muted)]", className)}>
        (empty diff)
      </pre>
    );
  }

  return (
    <div className={clsx("overflow-hidden rounded-xl bg-[var(--console-bg)] font-mono text-[12.5px]", className)}>
      <div className="flex items-center justify-between border-b border-white/[0.06] px-3 py-1.5">
        <span className="font-mono text-[10px] uppercase tracking-wide text-[var(--console-muted)]">Diff</span>
        <CopyButton text={diff} />
      </div>
      <div className="overflow-x-auto py-1.5">
        {hunks.map((hunk, hunkIndex) => (
          <div key={hunkIndex} className={hunkIndex > 0 ? "mt-2 border-t border-white/10 pt-2" : ""}>
            {hunk.header && (
              <LineRow line={{ kind: "hunk", content: hunk.header, oldLineNo: null, newLineNo: null }} cited={false} />
            )}
            {hunk.lines.map((line, lineIndex) => (
              <LineRow key={lineIndex} line={line} cited={isCitedLine(line, citedLines)} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
