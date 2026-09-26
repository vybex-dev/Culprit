// FILE: frontend/src/components/ui.tsx — place at this path in the Culprit repo
//
// Deliberately small. This dashboard's structure comes from hairline
// rules and section labels, not stacks of identically-rounded shadowed
// cards — see the hand-back notes for why.

import type { ReactNode } from "react";
import clsx from "clsx";

type PillVariant = "neutral" | "iris" | "butter" | "resolved" | "unresolved";

const PILL_STYLES: Record<PillVariant, string> = {
  neutral: "bg-ink/[0.06] text-ink",
  iris: "bg-iris text-paper",
  butter: "bg-butter-700/25 text-iris-700",
  resolved: "bg-resolved/15 text-resolved",
  unresolved: "bg-unresolved/15 text-unresolved",
};

export function Pill({
  children,
  variant = "neutral",
  className,
}: {
  children: ReactNode;
  variant?: PillVariant;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium leading-none",
        PILL_STYLES[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** A labeled hairline divider — the structural device this dashboard uses
 * instead of boxed cards. Sentence case on purpose (see frontend-design
 * skill: tracked-out all-caps eyebrows are one of the commonest
 * generated-page tells). */
export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx("flex items-center gap-3 text-[13px] text-muted", className)}>
      <span className="whitespace-nowrap">{children}</span>
      <span className="h-px flex-1 bg-line" aria-hidden />
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded bg-ink/[0.07]", className)} />;
}

export function IconButton({
  onClick,
  label,
  children,
  className,
}: {
  onClick: () => void;
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={clsx(
        "inline-flex h-8 w-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-ink/[0.06] hover:text-ink",
        className,
      )}
    >
      {children}
    </button>
  );
}
