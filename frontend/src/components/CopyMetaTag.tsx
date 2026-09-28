// FILE: frontend/src/components/CopyMetaTag.tsx
//
// A MetaTag you can click to copy the full value (the tag itself shows a
// shortened form — a truncated SHA or job id — so copying is the way to
// get the real thing). The "copied" confirmation is stacked on top of the
// original text instead of replacing it, so the chip never changes width
// and nothing around it shifts.

"use client";

import { useState, type ReactNode } from "react";
import clsx from "clsx";

export function CopyMetaTag({
  label,
  children,
  copyText,
  className,
}: {
  label?: string;
  children: ReactNode;
  /** The full value placed on the clipboard. */
  copyText: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(copyText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // Clipboard access can be denied — a convenience, not a requirement.
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={`${copyText} — click to copy`}
      className={clsx(
        "inline-flex max-w-full items-center gap-1.5 rounded-md border border-line bg-surface-2 px-2 py-1 font-mono text-[11px] leading-none text-muted transition-colors hover:border-line-strong hover:text-ink",
        className,
      )}
    >
      {label && <span className="shrink-0 text-muted/70">{label}</span>}
      <span className="grid min-w-0">
        <span className={clsx("col-start-1 row-start-1 truncate text-ink/80", copied && "invisible")}>{children}</span>
        <span
          className={clsx("col-start-1 row-start-1 text-resolved", !copied && "invisible")}
          role="status"
          aria-live="polite"
        >
          {copied ? "copied" : ""}
        </span>
      </span>
    </button>
  );
}
