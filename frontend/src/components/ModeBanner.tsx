// FILE: frontend/src/components/ModeBanner.tsx
//
// Honesty banner. When the backend runs in offline mode, the Nemotron roles are
// a labelled deterministic stand-in (backend/offline.py). Benchmarks are still
// real, but nobody should mistake a keyless demo for a Nemotron run — so it is
// said once, plainly, on every page.

"use client";

import { useBackendStatus } from "@/lib/useBackendStatus";

export function ModeBanner() {
  const { config } = useBackendStatus(false);
  if (config?.mode !== "offline") return null;
  return (
    <div className="border-b border-butter-700/30 bg-butter/25 px-4 py-1.5 text-center text-xs text-iris-700">
      <strong className="font-medium">Offline demo mode.</strong> Benchmarks are real measurements; the model reasoning is a
      labelled deterministic stand-in, <em>not</em> Nemotron.
    </div>
  );
}
