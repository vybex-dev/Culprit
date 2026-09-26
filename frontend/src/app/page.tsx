// FILE: frontend/src/app/page.tsx — place at this path in the Culprit repo

"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import { analyzeRepo, ApiError } from "@/lib/api";
import { SectionLabel } from "@/components/ui";

const TRACE_PREVIEW = [
  "bisector started — binary search over commit history",
  "commit 5daf6e3 scored 124.6 ms — +23.1% — regression candidate",
  "guilty commit located — 5daf6e3",
  "Nemotron 3 Ultra: analyzing guilty diff",
  "root cause classified — N+1 queries",
] as const;

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
        className="mt-1.5 w-full rounded-md border border-line-strong bg-surface px-3 py-2 font-mono text-sm text-ink placeholder:text-muted focus:border-iris"
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

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const commitRange: [string, string] | null = startSha.trim() && endSha.trim() ? [startSha.trim(), endSha.trim()] : null;
      const { job_id } = await analyzeRepo({
        repo_url: repoUrl.trim(),
        benchmark_command: benchmarkCommand.trim(),
        commit_range: commitRange,
      });
      router.push(`/job/${job_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong starting the analysis.");
      setSubmitting(false);
    }
  }

  const reduceMotion = useReducedMotion();

  return (
    <div className="mx-auto grid w-full max-w-5xl flex-1 grid-cols-1 items-center gap-12 px-6 py-16 lg:grid-cols-[minmax(0,1fr)_420px]">
      <div className="mx-auto flex w-full max-w-xl flex-col gap-6 lg:mx-0">
      <div>
        <h1 className="text-2xl font-medium tracking-tight text-ink">Find a performance regression</h1>
        <p className="mt-1.5 text-sm text-muted">
          Give it a repo and a benchmark command. It bisects the commit history, diagnoses the guilty commit, and
          tries to fix it.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">
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
          <SectionLabel>Commit range (optional)</SectionLabel>
          <div className="mt-3 grid grid-cols-2 gap-3">
            <Field
              label="Known-good"
              type="text"
              placeholder="a1b2c3d…"
              value={startSha}
              onChange={(e) => setStartSha(e.target.value)}
            />
            <Field
              label="Known-bad"
              type="text"
              placeholder="f9e8d7c…"
              value={endSha}
              onChange={(e) => setEndSha(e.target.value)}
            />
          </div>
          <p className="mt-1.5 text-xs text-muted">Leave both blank to bisect the full history.</p>
        </div>

        {error && (
          <p className="rounded-md border-l-4 border-unresolved bg-unresolved/[0.06] px-3 py-2 text-sm text-ink">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-iris px-4 py-2.5 text-sm font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {submitting ? "Starting…" : "Start analysis"}
        </button>
      </form>

      <p className="text-center text-xs text-muted">
        Don&apos;t have a backend running yet?{" "}
        <Link href="/dev/states" className="text-iris underline underline-offset-2">
          See what the dashboard looks like
        </Link>
        .
      </p>
      </div>

      <div className="hidden lg:block" aria-hidden>
        <div className="overflow-hidden rounded-lg border border-trace-line bg-trace-bg shadow-[0_1px_0_rgba(255,255,255,0.04)_inset]">
          <div className="flex items-center gap-2 border-b border-trace-line bg-trace-bg-raised px-3.5 py-2">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-trace-accent" />
            <span className="font-mono text-[11px] tracking-wide text-trace-muted">Agent activity</span>
          </div>
          <div className="space-y-2 px-3.5 py-3 font-mono text-[12.5px]">
            {TRACE_PREVIEW.map((line, i) => (
              <motion.div
                key={line}
                initial={reduceMotion ? false : { opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, delay: reduceMotion ? 0 : 0.25 + i * 0.35, ease: "easeOut" }}
                className="truncate text-trace-text"
              >
                <span className="text-trace-iris">›</span> {line}
              </motion.div>
            ))}
            <span className="trace-caret ml-[14px] inline-block h-[13px] w-[7px] translate-y-[2px] bg-trace-accent" />
          </div>
        </div>
        <p className="mt-3 text-center text-xs text-muted">
          You&apos;ll watch it work like this — every step, live, as it happens.
        </p>
      </div>
    </div>
  );
}
