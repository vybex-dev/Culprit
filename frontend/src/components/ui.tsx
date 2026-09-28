// FILE: frontend/src/components/ui.tsx
//
// Small, deliberately shared primitives. Structure in this dashboard
// comes from hairline rules, section labels and a fixed four-color
// status vocabulary (iris/butter/resolved/unresolved) — not stacks of
// identically-rounded shadowed cards.

import type { ReactNode } from "react";
import clsx from "clsx";

type PillVariant = "neutral" | "iris" | "butter" | "resolved" | "unresolved";

const PILL_STYLES: Record<PillVariant, string> = {
  neutral: "bg-ink/[0.06] text-ink",
  iris: "bg-iris text-paper",
  butter: "bg-butter-700/20 text-iris-700",
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
 * instead of boxed cards. Sentence case on purpose (tracked-out all-caps
 * eyebrows are one of the commonest generated-page tells). */
export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx("flex items-center gap-3 text-[13px] text-muted", className)}>
      <span className="whitespace-nowrap">{children}</span>
      <span className="h-px flex-1 bg-line" aria-hidden />
    </div>
  );
}

/** Shimmer-sweep loading block — reads as "content is being fetched",
 * distinct from a breathing dot (which reads as "this is live"). */
export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("shimmer rounded bg-ink/[0.06]", className)} />;
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

type DotVariant = "iris" | "butter" | "resolved" | "unresolved" | "neutral";

const DOT_BG: Record<DotVariant, string> = {
  iris: "bg-iris",
  butter: "bg-butter-700",
  resolved: "bg-resolved",
  unresolved: "bg-unresolved",
  neutral: "bg-ink/25",
};

/** A dot that breathes when `live`, ringed to visually cut out from
 * whatever's behind it. This is the app's single "something is actually
 * happening right now" signal — used in the header, mission control
 * lanes, and the trace feed header, so it means the same thing everywhere. */
export function LiveDot({
  variant = "iris",
  live = false,
  size = "sm",
  className,
}: {
  variant?: DotVariant;
  live?: boolean;
  size?: "sm" | "md";
  className?: string;
}) {
  const dim = size === "md" ? "h-2.5 w-2.5" : "h-1.5 w-1.5";
  return (
    <span className={clsx("relative inline-flex items-center justify-center", dim, className)} aria-hidden>
      {live && (
        <span className={clsx("breathe-ring absolute inset-0 rounded-full", DOT_BG[variant])} />
      )}
      <span className={clsx("relative rounded-full", dim, DOT_BG[variant], live && "breathe-dot")} />
    </span>
  );
}

/** A small mono data chip — replaces run-on "A · B · C" meta strings with
 * discrete, scannable tags. Used for benchmark command, commit range,
 * job id, and similar terse identifiers. */
export function MetaTag({
  label,
  children,
  title,
  className,
}: {
  label?: string;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex max-w-full items-center gap-1.5 rounded-md border border-line bg-surface-2 px-2 py-1 font-mono text-[11px] leading-none text-muted",
        className,
      )}
    >
      {label && <span className="shrink-0 text-muted/70">{label}</span>}
      <span className="truncate text-ink/80">{children}</span>
    </span>
  );
}
