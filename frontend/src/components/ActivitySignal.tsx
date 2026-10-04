"use client";

import { useEffect } from "react";
import { beginActivity } from "@/lib/activity";

/** Render this while work is in progress; the header logo animates meanwhile. */
export function ActivitySignal({ active = true }: { active?: boolean }) {
  useEffect(() => {
    if (!active) return;
    return beginActivity();
  }, [active]);
  return null;
}
