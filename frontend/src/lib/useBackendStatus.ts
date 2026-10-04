// FILE: frontend/src/lib/useBackendStatus.ts
//
// One place that asks the backend "who are you and are you ready?" — mode
// (live vs offline stand-in), sandbox backend, and the preflight checks
// (API key present? model IDs real? sandbox project set?). Used by the layout
// banner and the new-analysis page, so a misconfiguration is visible *before*
// a job is started, not three minutes into one.

"use client";

import { useEffect, useState } from "react";
import { apiBase, getConfig, getPreflight } from "./api";
import type { AppConfig, Preflight } from "./types";

export interface BackendStatus {
  loading: boolean;
  /** null ⇒ the backend couldn't be reached at all. */
  config: AppConfig | null;
  preflight: Preflight | null;
  unreachable: boolean;
  base: string;
}

export function useBackendStatus(withPreflight = false): BackendStatus {
  const [state, setState] = useState<Omit<BackendStatus, "base">>({
    loading: true,
    config: null,
    preflight: null,
    unreachable: false,
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [cfg, pre] = await Promise.allSettled([getConfig(), withPreflight ? getPreflight() : Promise.resolve(null)]);
      if (cancelled) return;
      setState({
        loading: false,
        config: cfg.status === "fulfilled" ? cfg.value : null,
        preflight: pre.status === "fulfilled" ? pre.value : null,
        unreachable: cfg.status === "rejected",
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [withPreflight]);

  return { ...state, base: apiBase() };
}
