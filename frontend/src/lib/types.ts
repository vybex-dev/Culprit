// FILE: frontend/src/lib/types.ts — place at this path in the Culprit repo
//
// Mirrors backend/api.py's Pydantic models field-for-field. That file, not
// docs/AGENT_SPECS.md §4 or docs/TRD.md §3, is the source of truth here —
// the two docs disagree with each other (see BUILD_00_OVERVIEW.md) and the
// implemented API differs from both in one place: it adds `error`, used
// only when status is "failed" (api.py's module docstring, flag #2).
// If backend/api.py's schema changes, update this file to match — don't
// let the two silently drift.

export type JobStatus =
  | "queued"
  | "bisecting"
  | "diagnosing"
  | "fixing"
  | "done"
  | "failed";

export type Confidence = "high" | "medium" | "low";

export type FinalResult = "resolved" | "unresolved_diagnosis_only";

// The Diagnoser's documented taxonomy (AGENT_SPECS.md §2). The backend
// does NOT constrain `category` to this union at the API layer (it's a
// plain `str` on DiagnosisModel) — diagnoser.py enforces it upstream, but
// the wire type is loose. Treat this as "the categories we know how to
// label nicely," not a guarantee; anything else must still render, just
// without a friendly label (see format.ts's categoryLabel).
export type KnownDiagnosisCategory =
  | "n_plus_one"
  | "lost_cache"
  | "algorithmic_complexity"
  | "blocking_call"
  | "allocation_overhead"
  | "other";

export interface TimelineEntry {
  commit: string;
  score: number;
  timestamp: string;
}

export interface TavilyRef {
  title: string;
  url: string;
}

export interface Diagnosis {
  category: string;
  explanation: string;
  cited_lines: string[];
  confidence: Confidence;
  tavily_refs: TavilyRef[];
  // NOT present on the real DiagnosisModel in backend/api.py today (verified
  // against the actual repo — confirmed absent from AGENT_SPECS.md §2's
  // output schema, TRD.md §3, and BUILD_00_OVERVIEW.md's reconciled shape
  // too). BUILD_03_FRONTEND_DASHBOARD.md asks for "the cited diff hunk,
  // render as an actual diff" as the panel's wow moment, which needs the
  // guilty commit's diff text somewhere in the contract — currently
  // nothing provides it to the frontend. Modeled here as optional so the
  // UI can use it the moment a backend owner adds it (see DiagnosisSection,
  // which degrades to a cited-lines-only view when this is absent) without
  // another type change. Flagged in the hand-back notes, not fabricated.
  diff?: string | null;
}

export interface FixAttempt {
  attempt: number;
  patch: string;
  rationale: string;
  score_after: number;
  resolved: boolean;
}

export interface Fix {
  patch_diff: string;
  verified: boolean;
  before_score: number;
  after_score: number;
}

export interface JobState {
  job_id: string;
  status: JobStatus;
  repo_url: string;
  benchmark_command: string;
  commit_range: [string, string] | null;
  timeline: TimelineEntry[];
  regression_commit: string | null;
  diagnosis: Diagnosis | null;
  fix_attempts: FixAttempt[];
  fix: Fix | null;
  final_result: FinalResult | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface AnalyzeRequest {
  repo_url: string;
  benchmark_command: string;
  commit_range: [string, string] | null;
}

export interface AnalyzeResponse {
  job_id: string;
}

// Derived, frontend-only view of state — never trust a "resolved" look
// unless this is true. Kept as a single function so there is exactly one
// place that encodes the rule from BUILD_03: "never show a resolved badge
// unless fix.verified === true."
export function isVerifiedResolved(job: JobState): boolean {
  return job.final_result === "resolved" && job.fix?.verified === true;
}

// "Regression found" is derived, not a status value of its own (per
// BUILD_00_OVERVIEW.md: "the dashboard's 'found regression' moment is
// derived by the frontend, not a separate status").
export function hasRegressionFound(job: JobState): boolean {
  return job.regression_commit !== null;
}
