// FILE: frontend/src/lib/format.ts — place at this path in the Culprit repo

import type { Confidence, KnownDiagnosisCategory } from "./types";

/** Benchmark methodology is wall-clock time (BUILD_00_OVERVIEW.md §"Two
 * open decisions"), so every score on the wire is milliseconds. */
export function formatScore(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatPctChange(before: number, after: number): string {
  if (before === 0) return "—";
  const pct = ((after - before) / before) * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

export function shortSha(sha: string, len = 7): string {
  return sha.slice(0, len);
}

export function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatElapsed(fromIso: string, toIso: string): string {
  const from = new Date(fromIso).getTime();
  const to = new Date(toIso).getTime();
  if (Number.isNaN(from) || Number.isNaN(to)) return "";
  const seconds = Math.max(0, Math.round((to - from) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) return `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

const CATEGORY_LABELS: Record<KnownDiagnosisCategory, string> = {
  n_plus_one: "N+1 queries",
  lost_cache: "Lost cache",
  algorithmic_complexity: "Algorithmic complexity",
  blocking_call: "Blocking call",
  allocation_overhead: "Allocation overhead",
  other: "Unclassified",
};

/** Category is a loose `string` on the wire (see types.ts) — this must
 * degrade gracefully for anything outside the known taxonomy rather than
 * throwing or rendering "undefined". */
export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category as KnownDiagnosisCategory] ?? category;
}

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};

export function truncateMiddle(text: string, max = 64): string {
  if (text.length <= max) return text;
  const half = Math.floor((max - 1) / 2);
  return `${text.slice(0, half)}…${text.slice(text.length - half)}`;
}

/** repo_url can be a real git remote or a local path (backend/api.py
 * accepts either — see its module docstring, flag #4). This shows
 * "org/repo" for a recognizable host, or the last path segment otherwise,
 * rather than a raw URL that pushes everything else off-screen. */
export function repoDisplayName(repoUrl: string): string {
  const cleaned = repoUrl.replace(/\.git$/, "").replace(/\/+$/, "");
  const segments = cleaned.split(/[/\\]/).filter(Boolean);
  if (segments.length >= 2 && /^(github|gitlab|bitbucket)/i.test(segments[segments.length - 3] ?? "")) {
    return segments.slice(-2).join("/");
  }
  if (segments.length >= 2 && (cleaned.startsWith("http") || cleaned.includes("@"))) {
    return segments.slice(-2).join("/");
  }
  return segments[segments.length - 1] ?? repoUrl;
}
