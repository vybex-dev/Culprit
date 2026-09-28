// FILE: frontend/src/app/new/page.tsx
//
// The "start an analysis" form. Lives at /new — the marketing landing
// page now owns "/" (see that file) and routes "Get started" here.

"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import { analyzeRepo, ApiError } from "@/lib/api";
import { LiveDot, SectionLabel } from "@/components/ui";

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

export default function NewAnalysisPage() {
  const router = useRouter();
  const [repoUrl, setRepoUrl] = useState("");
  const [benchmarkCommand, setBenchmarkCommand] = useState("");
  const [startSha, setStartSha] = useState("");
  const [endSha, setEndSha] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reduceMotion = useReducedMotion();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const { job_id } = await analyzeRepo({
        repo_url: repoUrl.trim(),
        benchmark_command: benchmarkCommand.trim(),
        commit_range: [startSha.trim(), endSha.trim()],
      });
      router.push(`/job/${job_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong starting the analysis.");
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center gap-6 px-6 py-16">
      <div>
        <h1 className="text-2xl font-medium tracking-tight text-ink">Find a performance regression</h1>
        <p className="mt-1.5 text-sm text-muted">
          Give it a repo and a benchmark command. It bisects the commit history, diagnoses the guilty commit, and
          tries to fix it.
        </p>
      </div>

      <motion.form
        onSubmit={handleSubmit}
        className="relative space-y-5 overflow-hidden rounded-2xl border border-line bg-surface-2 p-5"
        initial={reduceMotion ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, ease: "easeOut" }}
      >
        <div className="mc-grid pointer-events-none absolute inset-0 opacity-50" aria-hidden />
        <div className="relative space-y-5">
          <Field
            label="Repository"
            hint="A git URL the backend can clone."
            type="text"
            required
            placeholder="https://github.com/org/repo"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
          />
          <Field
            label="Benchmark command"
            hint="Should print a single wall-clock number the bisector can compare across commits."
            type="text"
            required
            placeholder="pytest benchmarks/test_hot_path.py --benchmark-only"
            value={benchmarkCommand}
            onChange={(e) => setBenchmarkCommand(e.target.value)}
          />

          <div>
            {/* CODE_REVIEW_FINDINGS.md #2: the backend has no "auto-detect a
                range" behavior (api.py's module docstring flag #3 — nothing
                in TRD/AGENT_SPECS says what that should even mean), so
                inviting the person to leave both blank here just produced a
                guaranteed 400 from POST /analyze. Both fields are required
                until that backend feature is actually designed and built. */}
            <SectionLabel>Commit range</SectionLabel>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Field
                label="Known-good"
                type="text"
                required
                placeholder="a1b2c3d…"
                value={startSha}
                onChange={(e) => setStartSha(e.target.value)}
              />
              <Field
                label="Known-bad"
                type="text"
                required
                placeholder="f9e8d7c…"
                value={endSha}
                onChange={(e) => setEndSha(e.target.value)}
              />
            </div>
            <p className="mt-1.5 text-xs text-muted">
              Both are required for now — there&apos;s no automatic range detection yet.
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
            disabled={submitting}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-iris px-4 py-2.5 text-sm font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-70"
          >
            {submitting && <LiveDot variant="butter" live />}
            {submitting ? "Starting a sandbox…" : "Start analysis"}
          </button>
        </div>
      </motion.form>

      <p className="text-center text-xs text-muted">
        Don&apos;t have a backend running yet?{" "}
        <Link href="/dev/states" className="text-iris underline underline-offset-2">
          See what the dashboard looks like
        </Link>
        .
      </p>
    </div>
  );
}
