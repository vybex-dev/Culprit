// FILE: frontend/src/lib/api.ts — place at this path in the Culprit repo
//
// Thin client for backend/api.py. Base URL comes from
// NEXT_PUBLIC_API_BASE_URL (see .env.local.example) — per
// BUILD_00_OVERVIEW.md's shared env vars, e.g. http://localhost:8000.

import type { AnalyzeRequest, AnalyzeResponse, JobState } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function apiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!base) {
    throw new ApiError(
      0,
      "NEXT_PUBLIC_API_BASE_URL is not set — copy .env.local.example to .env.local and point it at the backend.",
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
