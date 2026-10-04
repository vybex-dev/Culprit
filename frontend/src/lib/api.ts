// FILE: frontend/src/lib/api.ts — place at this path in the Culprit repo
//
// Thin client for backend/api.py. Base URL comes from
// NEXT_PUBLIC_API_BASE_URL (see .env.example — CODE_REVIEW_FINDINGS.md
// #12: this comment used to point at ".env.local.example", a file that
// never existed; frontend/README.md's own setup steps already say
// ".env.example", so that's the name standardized on here too) — per
// BUILD_00_OVERVIEW.md's shared env vars, e.g. http://localhost:8000.

import type { AnalyzeRequest, AnalyzeResponse, AppConfig, EventsPage, JobState, JobSummary, Preflight } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function apiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!base) {
    throw new ApiError(
      0,
      "NEXT_PUBLIC_API_BASE_URL is not set — copy .env.example to .env.local and point it at the backend.",
    );
  }
  return base.replace(/\/+$/, "");
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    // FastAPI's HTTPException serializes as {"detail": "..."}.
    if (typeof body?.detail === "string") return body.detail;
    return JSON.stringify(body);
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

export async function analyzeRepo(req: AnalyzeRequest): Promise<AnalyzeResponse> {
  const res = await fetch(`${apiBase()}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new ApiError(res.status, await parseErrorDetail(res));
  return res.json();
}

export async function getJob(jobId: string): Promise<JobState> {
  const res = await fetch(`${apiBase()}/jobs/${encodeURIComponent(jobId)}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new ApiError(res.status, await parseErrorDetail(res));
  return res.json();
}

export async function getEvents(jobId: string, after: number, signal?: AbortSignal): Promise<EventsPage> {
  const res = await fetch(`${apiBase()}/jobs/${encodeURIComponent(jobId)}/events?after=${after}&limit=500`, {
    cache: "no-store",
    signal,
  });
  if (!res.ok) throw new ApiError(res.status, await parseErrorDetail(res));
  return res.json();
}

export async function cancelJob(jobId: string): Promise<void> {
  const res = await fetch(`${apiBase()}/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
  if (!res.ok) throw new ApiError(res.status, await parseErrorDetail(res));
}

export async function startDemo(): Promise<AnalyzeResponse> {
  const res = await fetch(`${apiBase()}/demo`, { method: "POST" });
  if (!res.ok) throw new ApiError(res.status, await parseErrorDetail(res));
  return res.json();
}

export async function listJobs(limit = 50): Promise<JobSummary[]> {
  const res = await fetch(`${apiBase()}/jobs?limit=${limit}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, await parseErrorDetail(res));
  return res.json();
}

export async function getConfig(): Promise<AppConfig> {
  const res = await fetch(`${apiBase()}/config`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, await parseErrorDetail(res));
  return res.json();
}

export async function getPreflight(): Promise<Preflight> {
  const res = await fetch(`${apiBase()}/preflight`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, await parseErrorDetail(res));
  return res.json();
}

/** Download URLs served by the backend (report.md / fix.patch). */
export function reportUrl(jobId: string): string {
  return `${apiBase()}/jobs/${encodeURIComponent(jobId)}/report.md`;
}
export function patchUrl(jobId: string): string {
  return `${apiBase()}/jobs/${encodeURIComponent(jobId)}/fix.patch`;
}

export async function fetchReportMarkdown(jobId: string): Promise<string> {
  const res = await fetch(reportUrl(jobId), { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, await parseErrorDetail(res));
  return res.text();
}
