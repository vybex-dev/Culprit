"use client";

import { useSyncExternalStore } from "react";
import { LogoLoader } from "@/components/LogoLoader";
import {
  getActivityPlaying,
  getServerActivityPlaying,
  settleActivity,
  subscribeActivity,
} from "@/lib/activity";

/** Header logo: still normally, spinning while an analysis is running. */
export function HeaderLogo({ size = 28 }: { size?: number }) {
  const playing = useSyncExternalStore(subscribeActivity, getActivityPlaying, getServerActivityPlaying);
  return (
    // Each loop cycle ends exactly in the logo pose; if work has finished by
    // then, stop there so the logo never freezes mid-spin.
    <span className="inline-flex" onAnimationIteration={settleActivity}>
      <LogoLoader
        size={size}
        mode={playing ? "loop" : "static"}
        label={playing ? "Analysis running" : "Culprit"}
      />
    </span>
  );
}
