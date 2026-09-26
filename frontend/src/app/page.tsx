// FILE: frontend/src/app/page.tsx — place at this path in the Culprit repo

"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { analyzeRepo, ApiError } from "@/lib/api";
import { SectionLabel } from "@/components/ui";

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

  return (
    <div className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center gap-6 px-6 py-16">
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
  );
}
