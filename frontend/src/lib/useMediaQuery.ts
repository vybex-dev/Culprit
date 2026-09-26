// FILE: frontend/src/lib/useMediaQuery.ts — place at this path in the Culprit repo
//
// Used to pick the drill-down panel's slide axis: bottom-sheet on mobile,
// right-drawer on desktop. Built on useSyncExternalStore rather than
// useEffect+setState — the canonical way to subscribe to a browser API
// like matchMedia, and it sidesteps eslint-plugin-react-hooks's
// set-state-in-effect rule entirely rather than working around it.

"use client";

import { useSyncExternalStore } from "react";

function subscribe(query: string, onChange: () => void) {
  const mql = window.matchMedia(query);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

export function useMediaQuery(query: string, serverSnapshot = false): boolean {
  return useSyncExternalStore(
    (onChange) => subscribe(query, onChange),
    () => window.matchMedia(query).matches,
    () => serverSnapshot,
  );
}
