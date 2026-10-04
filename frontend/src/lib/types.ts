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
  | "failed"
  | "cancelled";

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
  /** What the source says (bounded to ~400 chars by the backend). */
  snippet?: string;
  /** Tavily's own 0..1 relevance score for this result. */
  score?: number | null;
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
  /** Non-empty ⇒ the attempt never produced a measurement (the patch didn't
   * apply, or the patched code crashed) and `score_after` is NOT one. */
  error?: string | null;
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
  /** ISO time each stage was entered, keyed by status; "done"/"failed"
   * mark the end of the run. Absent on older backends — the UI then just
   * omits per-stage durations. */
  stage_times?: Partial<Record<JobStatus, string>>;
  /** Server clock when this response was produced — used to correct for
   * clock skew so the live timer starts at the right number. */
  server_time?: string | null;

  // --- Live-pipeline additions (all optional: fixtures and older backends
  // simply lack them and the UI degrades to the coarse timeline). ---
  /** "offline" ⇒ model roles were the labelled deterministic stand-in. */
  mode?: "live" | "offline";
  /** Every commit in the searched range; index 0 is the known-good start. */
  commits?: Commit[];
  /** Each benchmarked commit's raw runs + verdict, in probe order. */
  probes?: Probe[];
  baseline_score?: number | null;
  threshold_pct?: number | null;
  n_runs?: number | null;
  /** [lo, hi] indices still under suspicion (hi < lo ⇒ converged). */
  window?: [number, number] | null;
  cancel_requested?: boolean;
  metrics?: Metrics | null;
  label?: string | null;
}

export interface Commit {
  index: number;
  sha: string;
  subject: string;
  author: string;
  date: string;
}

export type Verdict = "baseline" | "clean" | "regressed";

export interface NanoCall {
  model_id: string;
  latency_s: number;
  nano_verdict: string;
  final_verdict: string;
  /** true ⇒ the small model's verdict contradicted plain arithmetic and was overridden. */
  overridden: boolean;
  n_scores: number;
  offline?: boolean;
}

export interface Probe {
  step: number;
  commit: string;
  index: number;
  role: "baseline" | "endpoint" | "bisect";
  raw_scores: number[];
  median_score: number;
  pct_change: number;
  verdict: Verdict | string;
  rounds: number;
  nano: NanoCall[];
  window?: [number, number] | null;
  timestamp: string;
  wall_s: number;
}

export interface ModelMetrics {
  role: string;
  model_id: string;
  calls: number;
  latency_s: number;
  avg_latency_s: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  retries: number;
  offline: boolean;
}

export interface Metrics {
  models: Record<string, ModelMetrics>;
  sandbox: { probes: number; runs: number; patched_runs: number; instances: number; run_wall_s: number };
  tavily: { searches: number; sources: number };
  git_calls: number;
  events: number;
}

/** One entry of the live activity stream (backend/events.py). */
export interface LogEvent {
  seq: number;
  ts: string;
  kind: string;
  source: string;
  message: string;
  data: Record<string, unknown>;
}

export interface EventsPage {
  events: LogEvent[];
  next: number;
  done: boolean;
  status: JobStatus;
}

export interface JobSummary {
  job_id: string;
  status: JobStatus;
  repo_url: string;
  created_at: string;
  updated_at: string;
  mode: "live" | "offline";
  regression_commit: string | null;
  final_result: FinalResult | null;
  category: string | null;
  before_score: number | null;
  regressed_score: number | null;
  after_score: number | null;
  n_commits: number;
  n_probes: number;
  subject: string | null;
  label: string | null;
}

export interface AppConfig {
  mode: "live" | "offline";
  sandbox_backend: "token_factory" | "local";
  threshold_pct: number;
  n_runs: number;
  auto_range_commits: number;
  max_concurrent_jobs: number;
  demo_benchmark_command: string;
}

export interface PreflightCheck {
  name: string;
  status: "ok" | "warn" | "fail" | "unknown";
  detail: string;
  catalog_matches?: string[];
  missing?: Record<string, string>;
}

export interface Preflight {
  ready: boolean;
  mode: "live" | "offline";
  sandbox_backend: "token_factory" | "local";
  checks: PreflightCheck[];
}

export interface AnalyzeRequest {
  repo_url: string;
  benchmark_command: string;
  /** null ⇒ the backend auto-detects the range (last N first-parent commits). */
  commit_range: [string, string] | null;
  label?: string;
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

/** A job that will never change again. */
export function isTerminal(status: JobStatus): boolean {
  return status === "done" || status === "failed" || status === "cancelled";
}
