// FILE: frontend/src/components/dashboard/DrillDownPanel.tsx — place at this path in the Culprit repo
//
// Slides in as an overlay drawer rather than pushing the timeline's grid
// column — simpler to get the motion right for both breakpoints, and
// still satisfies BUILD_03's "drill-down side panel, opened by clicking
// the regression point." Axis (x on desktop, y on mobile) is chosen once
// via a media query rather than animating both at once, so the two
// breakpoints' transforms never fight each other.

"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { IconButton } from "@/components/ui";

export function DrillDownPanel({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const isDesktop = useMediaQuery("(min-width: 640px)");
  const reduceMotion = useReducedMotion();

  const offscreen = reduceMotion ? { opacity: 0 } : isDesktop ? { x: "100%" } : { y: "100%" };
  const onscreen = reduceMotion ? { opacity: 1 } : isDesktop ? { x: 0 } : { y: 0 };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-30 bg-ink/30"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.2, ease: "easeOut" }}
            onClick={onClose}
            aria-hidden
          />
          <motion.aside
            role="dialog"
            aria-modal="true"
            aria-label={title}
            className="fixed inset-x-0 bottom-0 z-40 flex max-h-[85vh] flex-col rounded-t-xl border-t border-line bg-paper shadow-xl sm:inset-y-0 sm:left-auto sm:right-0 sm:max-h-none sm:w-[480px] sm:rounded-none sm:border-l sm:border-t-0"
            initial={offscreen}
            animate={onscreen}
            exit={offscreen}
            transition={{
              duration: reduceMotion ? 0.01 : 0.32,
              // Ease-out entering, ease-in leaving (frontend-design skill).
              ease: reduceMotion ? "linear" : open ? "easeOut" : "easeIn",
            }}
          >
            <div className="flex shrink-0 items-center justify-between border-b border-line px-4 py-3">
              <h2 className="text-sm font-medium text-ink">{title}</h2>
              <IconButton label="Close" onClick={onClose}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
                  <path d="M4 4L12 12M12 4L4 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </IconButton>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
