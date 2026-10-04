// FILE: frontend/src/app/new/page.tsx
//
// The "start an analysis" screen. Three jobs:
//   1. one click to the bundled demo — a real git history with a real,
//      measured regression — so anyone can see the whole pipeline immediately;
//   2. a plain form for their own repo (commit range optional: leave it blank
//      and the backend searches the last N commits);
//   3. say honestly whether the backend is ready *before* spending anything:
//      preflight verifies the API key, the Nemotron model IDs against the live
//      catalog, and the sandbox config.

"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import clsx from "clsx";
import { motion, useReducedMotion } from "motion/react";
import { analyzeRepo, ApiError, startDemo } from "@/lib/api";
import { useBackendStatus } from "@/lib/useBackendStatus";
import type { PreflightCheck } from "@/lib/types";
import { LiveDot, SectionLabel } from "@/components/ui";
import { ActivitySignal } from "@/components/ActivitySignal";

function Field({
  label,
  hint,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-ink">{label}</span>
      {hint && <span className="mt-0.5 block text-xs text-muted">{hint}</span>}
      <input
        {...props}
        className="mt-1.5 w-full rounded-md border border-line-strong bg-surface px-3 py-2 font-mono text-sm text-ink placeholder:text-muted/70 transition-colors focus:border-iris"
      />
    </label>
  );
}

const CHECK_LABEL: Record<string, string> = {
  git: "git",
  api_key: "Nebius API key",
  models: "Nemotron model IDs",
  sandbox: "Sandboxes",
  sandbox_image: "Sandbox image",
  tavily: "Tavily",
};

const CHECK_STYLE: Record<PreflightCheck["status"], { dot: string; text: string; mark: string }> = {
  ok: { dot: "bg-resolved", text: "text-ink", mark: "✓" },
  warn: { dot: "bg-butter-700", text: "text-ink", mark: "!" },
  unknown: { dot: "bg-ink/30", text: "text-ink", mark: "?" },
  fail: { dot: "bg-unresolved", text: "text-ink", mark: "✗" },
};

function StatusPanel() {
  const { loading, config, preflight, unreachable, base } = useBackendStatus(true);

  if (loading) {
    return <div className="shimmer h-24 rounded-xl border border-line bg-surface-2" aria-label="Checking the backend" />;
  }
  if (unreachable) {
    return (
      <div className="glow-unresolved rounded-xl border border-transparent bg-unresolved/[0.08] px-4 py-3 text-sm">
        <p className="font-medium text-ink">Can&apos;t reach the backend at {base}.</p>
        <p className="mt-1 text-xs text-muted">
          Start it with <code className="font-mono">python api.py</code> in <code className="font-mono">backend/</code>, or set{" "}
          <code className="font-mono">NEXT_PUBLIC_API_BASE_URL</code>. No backend yet?{" "}
          <a href="/dev/states" className="text-iris underline underline-offset-2">
            Browse the dashboard states
          </a>
          .
        </p>
      </div>
    );
  }
  if (!preflight) return null;

  const failing = preflight.checks.filter((c) => c.status === "fail");
  return (
    <details className="group rounded-xl border border-line bg-surface" open={failing.length > 0}>
      <summary className="flex cursor-pointer list-none items-center gap-2.5 px-4 py-2.5 text-sm">
        <LiveDot variant={preflight.ready ? "resolved" : "unresolved"} />
        <span className="font-medium text-ink">{preflight.ready ? "Backend ready" : "Backend needs attention"}</span>
        <span className="text-xs text-muted">
          {config?.mode === "offline" ? "offline stand-in models" : "Nemotron on Token Factory"} ·{" "}
          {preflight.sandbox_backend === "token_factory" ? "Token Factory sandboxes" : "local sandbox"}
        </span>
        <span className="ml-auto text-xs text-muted group-open:hidden">details</span>
      </summary>
      <ul className="space-y-2 border-t border-line px-4 py-3">
        {preflight.checks.map((c) => {
          const st = CHECK_STYLE[c.status];
          return (
            <li key={c.name} className="flex gap-2.5 text-xs">
              <span className={clsx("mt-1 h-1.5 w-1.5 shrink-0 rounded-full", st.dot)} aria-hidden />
              <div className="min-w-0">
                <span className="font-medium text-ink">{CHECK_LABEL[c.name] ?? c.name}</span>
                <span className="ml-1.5 text-muted">{c.detail}</span>
                {c.catalog_matches && c.catalog_matches.length > 0 && c.status === "fail" && (
                  <div className="mt-1 break-all font-mono text-[11px] text-muted">
                    available: {c.catalog_matches.join(", ")}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

export default function NewAnalysisPage() {
  const router = useRouter();
  const { config } = useBackendStatus(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [benchmarkCommand, setBenchmarkCommand] = useState("");
  const [startRev, setStartRev] = useState("");
  const [endRev, setEndRev] = useState("");
  const [submitting, setSubmitting] = useState<"form" | "demo" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reduceMotion = useReducedMotion();

  const oneEnd = Boolean(startRev.trim()) !== Boolean(endRev.trim());

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (oneEnd) {
      setError("Fill in both ends of the commit range, or leave both blank to search the most recent commits.");
      return;
    }
    setSubmitting("form");
    try {
      const { job_id } = await analyzeRepo({
        repo_url: repoUrl.trim(),
        benchmark_command: benchmarkCommand.trim(),
        commit_range: startRev.trim() ? [startRev.trim(), endRev.trim()] : null,
      });
      router.push(`/job/${job_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong starting the analysis.");
      setSubmitting(null);
    }
  }

  async function handleDemo() {
    setError(null);
    setSubmitting("demo");
    try {
      const { job_id } = await startDemo();
      router.push(`/job/${job_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't start the demo — is the backend running?");
      setSubmitting(null);
    }
  }

  const autoN = config?.auto_range_commits ?? 30;

  return (
    <div className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center gap-6 px-6 py-12">
      <ActivitySignal active={submitting !== null} />
      <div>
        <h1 className="text-2xl font-medium tracking-tight text-ink">Find a performance regression</h1>
        <p className="mt-1.5 text-sm text-muted">
          Give it a repo and a benchmark command. It bisects the commit history, diagnoses the guilty commit, and tries to
          fix it — and proves the fix with a real re-run.
        </p>
      </div>

      <StatusPanel />

      <motion.div
        className="relative overflow-hidden rounded-2xl border border-line bg-iris-100/60 p-5"
        initial={reduceMotion ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, ease: "easeOut" }}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-sm font-medium text-ink">See it work in about a minute</h2>
            <p className="mt-1 text-xs leading-relaxed text-muted">
              A bundled repo with a real history of 20 commits and one genuine N+1 regression hidden in a “simplify loading”
              refactor. Every benchmark number is a real measurement.
              {config?.mode === "offline" && " Runs with no API keys — the model reasoning is a labelled stand-in."}
            </p>
          </div>
          <button
            type="button"
            onClick={handleDemo}
            disabled={submitting !== null}
            className="flex shrink-0 items-center gap-2 rounded-md bg-iris px-4 py-2 text-sm font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-70"
          >
            {submitting === "demo" && <LiveDot variant="butter" live />}
            {submitting === "demo" ? "Starting…" : "Run the demo"}
          </button>
        </div>
      </motion.div>

      <motion.form
        onSubmit={handleSubmit}
        className="relative space-y-5 overflow-hidden rounded-2xl border border-line bg-surface-2 p-5"
        initial={reduceMotion ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, delay: 0.05, ease: "easeOut" }}
      >
        <div className="mc-grid pointer-events-none absolute inset-0 opacity-50" aria-hidden />
        <div className="relative space-y-5">
          <Field
            label="Repository"
            hint="An https:// or ssh git URL the backend can clone."
            type="text"
            required
            placeholder="https://github.com/org/repo"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
          />
          <Field
            label="Benchmark command"
            hint="Run at every commit; must print a single wall-clock number (ms) as its last line."
            type="text"
            required
            placeholder="python bench.py"
            value={benchmarkCommand}
            onChange={(e) => setBenchmarkCommand(e.target.value)}
          />

          <div>
            <SectionLabel>Commit range (optional)</SectionLabel>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Field
                label="Known-good"
                type="text"
                placeholder={`HEAD~${autoN}`}
                value={startRev}
                onChange={(e) => setStartRev(e.target.value)}
                aria-invalid={oneEnd || undefined}
              />
              <Field
                label="Known-bad"
                type="text"
                placeholder="HEAD"
                value={endRev}
                onChange={(e) => setEndRev(e.target.value)}
                aria-invalid={oneEnd || undefined}
              />
            </div>
            <p className="mt-1.5 text-xs text-muted">
              SHAs, tags or branches. Leave both blank to search the last {autoN} commits up to HEAD.
            </p>
          </div>

          {error && (
            <div className="glow-unresolved flex items-start gap-2.5 rounded-xl border border-transparent bg-unresolved/[0.08] px-3.5 py-2.5">
              <span className="mt-0.5 text-unresolved" aria-hidden>
                ✕
              </span>
              <p className="text-sm text-ink">{error}</p>
            </div>
          )}

          <button
            type="submit"
            disabled={submitting !== null}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-iris px-4 py-2.5 text-sm font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-70"
          >
            {submitting === "form" && <LiveDot variant="butter" live />}
            {submitting === "form" ? "Starting a sandbox…" : "Start analysis"}
          </button>
        </div>
      </motion.form>
    </div>
  );
}
