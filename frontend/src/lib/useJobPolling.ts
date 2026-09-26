// FILE: frontend/src/lib/useJobPolling.ts — place at this path in the Culprit repo

"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, getJob } from "./api";
import type { JobState } from "./types";

const POLL_INTERVAL_MS = 2000;
const TERMINAL_STATUSES = new Set(["done", "failed"]);

interface UseJobPollingResult {
  job: JobState | null;
  /** Set only when we have never successfully loaded the job at all —
   * once we have a job, later fetch failures become `isReconnecting`
   * instead, so a flaky network doesn't blank out a job mid-run. */
  fatalError: string | null;
  isReconnecting: boolean;
  isPolling: boolean;
}

export function useJobPolling(jobId: string): UseJobPollingResult {
  const [job, setJob] = useState<JobState | null>(null);
  const [fatalError, setFatalError] = useState<string | null>(null);
  const [isReconnecting, setIsReconnecting] = useState(false);
  const jobRef = useRef<JobState | null>(null);
  // Tracks which jobId the *last successful* fetch belongs to, so a poll
  // failure right after switching jobId reads as "never loaded" rather
  // than "reconnecting" — without needing a synchronous state reset at
  // the top of the effect (see useMediaQuery.ts's comment on why that's
  // avoided; here the fix is deriving staleness instead of resetting it).
  const loadedForJobIdRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function tick() {
      try {
        const next = await getJob(jobId);
        if (cancelled) return;
        jobRef.current = next;
        loadedForJobIdRef.current = jobId;
        setJob(next);
        setIsReconnecting(false);
        setFatalError(null);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof ApiError ? err.message : "Could not reach the backend.";
        if (loadedForJobIdRef.current === jobId) {
          setIsReconnecting(true);
        } else {
          setFatalError(message);
        }
      } finally {
        if (cancelled) return;
        const status = loadedForJobIdRef.current === jobId ? jobRef.current?.status : undefined;
        if (!status || !TERMINAL_STATUSES.has(status)) {
          timer = setTimeout(tick, POLL_INTERVAL_MS);
        }
      }
    }

    tick();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  // If jobId changes but the component isn't remounted, `job` may still
  // hold the previous job for a moment — every JobState carries its own
  // job_id, so staleness is derived here rather than cleared in the effect.
  const isStale = job !== null && job.job_id !== jobId;
  const currentJob = isStale ? null : job;
  const status = currentJob?.status;

  return {
    job: currentJob,
    fatalError: isStale ? null : fatalError,
    isReconnecting: isStale ? false : isReconnecting,
    isPolling: !!status && !TERMINAL_STATUSES.has(status),
  };
}
