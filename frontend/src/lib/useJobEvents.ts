// FILE: frontend/src/lib/useJobEvents.ts
//
// Follows a job's live activity stream (GET /jobs/{id}/events?after=<seq>).
//
// The stream is cursor-paged and append-only, so this can never miss or
// double-render an event: it asks for "everything after the last seq I saw"
// and stops only when the server says the job is terminal AND the cursor has
// caught up (`done`). A flaky network just delays the next poll — events
// already received are kept, and the cursor resumes where it left off.

"use client";

import { useEffect, useState } from "react";
import { getEvents } from "./api";
import type { LogEvent } from "./types";

const POLL_MS = 700;
const ERROR_BACKOFF_MS = 2000;

interface Stream {
  jobId: string;
  events: LogEvent[];
  done: boolean;
}

export function useJobEvents(jobId: string): { events: LogEvent[]; done: boolean } {
  const [stream, setStream] = useState<Stream>({ jobId, events: [], done: false });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    let cursor = 0;

    async function tick() {
      let nextDelay = POLL_MS;
      try {
        const page = await getEvents(jobId, cursor, controller.signal);
        if (cancelled) return;
        if (page.events.length > 0) {
          cursor = page.next;
          setStream((prev) => {
            const base = prev.jobId === jobId ? prev.events : [];
            const have = new Set(base.map((e) => e.seq));
            const fresh = page.events.filter((e) => !have.has(e.seq));
            return { jobId, events: fresh.length ? [...base, ...fresh] : base, done: page.done };
          });
          // A full page means there's more waiting — fetch it right away.
          if (page.events.length >= 500) nextDelay = 0;
        } else if (page.done) {
          setStream((prev) => (prev.jobId === jobId ? { ...prev, done: true } : { jobId, events: [], done: true }));
        }
        if (page.done) return; // terminal and fully drained — stop polling
      } catch {
        if (cancelled) return;
        nextDelay = ERROR_BACKOFF_MS;
      }
      timer = setTimeout(tick, nextDelay);
    }

    tick();
    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  // If jobId changed but the effect hasn't produced anything yet, don't show
  // the previous job's log.
  return stream.jobId === jobId ? { events: stream.events, done: stream.done } : { events: [], done: false };
}
