// FILE: frontend/src/app/page.tsx — place at this path in the Culprit repo
//
// Marketing landing page. The form lives at /new (see that file) —
// "Get started" below routes there. Every concrete number, commit hash,
// and trace line on this page is pulled from fixtures/done-resolved.ts
// (the same fixture the dashboard's own dev/states reference uses), not
// written separately — so the landing page can never show a "demo" that
// contradicts what the product actually renders once wired to a real
// backend.

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { doneResolvedJob } from "@/fixtures/done-resolved";
import { buildTrace } from "@/lib/trace";
import { formatPctChange, formatScore, shortSha } from "@/lib/format";
import { TraceFeed } from "@/components/dashboard/TraceFeed";

const HERO_TRACE = buildTrace(doneResolvedJob).slice(0, 6);
const REGRESSED = doneResolvedJob.timeline.find((e) => e.commit === doneResolvedJob.regression_commit)!;
const BASELINE = doneResolvedJob.timeline[doneResolvedJob.timeline.indexOf(REGRESSED) - 1];

const STAGES = [
  {
    n: "01",
    name: "Bisect",
    body: "Binary-searches the commit history in isolated sandboxes until it lands on the exact commit where the benchmark got worse.",
  },
  {
    n: "02",
    name: "Diagnose",
    body: "Reads the guilty diff in full file context and names the root cause — N+1 queries, a lost cache, an O(n²) creeping in — citing the exact lines responsible.",
  },
  {
    n: "03",
    name: "Fix & verify",
    body: 'Proposes a patch, applies it in a fresh sandbox, and re-runs the benchmark. Nothing is called "fixed" until a real re-run proves it.',
  },
] as const;

function useLoopedHeroTrace() {
  const [cycle, setCycle] = useState(0);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    if (reduceMotion) return;
    const totalMs = 900 + HERO_TRACE.length * 650;
    const t = setTimeout(() => setCycle((c) => c + 1), totalMs);
    return () => clearTimeout(t);
  }, [cycle, reduceMotion]);

  return cycle;
}

export default function LandingPage() {
  const cycle = useLoopedHeroTrace();
  const reduceMotion = useReducedMotion();

  return (
    <div className="flex flex-1 flex-col">
      {/* Hero */}
      <section className="mx-auto grid w-full max-w-6xl grid-cols-1 items-center gap-14 px-6 pb-20 pt-16 lg:grid-cols-[minmax(0,1fr)_460px] lg:pt-24">
        <div className="max-w-xl">
          <p className="font-mono text-xs tracking-wide text-iris-700">Culprit</p>
          <h1 className="mt-3 text-4xl font-medium leading-[1.08] tracking-tight text-ink sm:text-5xl">
            Finds the exact commit that made your app slower.
          </h1>
          <p className="mt-5 text-base leading-relaxed text-muted">
            Point it at a repo and a benchmark. It bisects the history, explains the regression in plain English
            with the lines to prove it, and verifies a fix in a sandbox before it tells you it&apos;s done.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-4">
            <Link
              href="/new"
              className="rounded-md bg-iris px-5 py-2.5 text-sm font-medium text-paper transition-opacity hover:opacity-90"
            >
              Get started
            </Link>
            <Link
              href="/dev/states"
              className="text-sm text-iris underline decoration-iris/30 underline-offset-4 hover:decoration-iris"
            >
              See the dashboard first
            </Link>
          </div>
          <dl className="mt-12 grid max-w-md grid-cols-3 gap-6 border-t border-line pt-6">
            <div>
              <dt className="text-xs text-muted">Regression found at</dt>
              <dd className="mt-1 font-mono text-sm text-ink">{shortSha(REGRESSED.commit)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Benchmark</dt>
              <dd className="mt-1 font-mono text-sm text-ink">
                {formatScore(BASELINE.score)} → {formatScore(REGRESSED.score)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Change</dt>
              <dd className="mt-1 font-mono text-sm text-unresolved">
                {formatPctChange(BASELINE.score, REGRESSED.score)}
              </dd>
            </div>
          </dl>
        </div>

        <div>
          <TraceFeed key={cycle} events={HERO_TRACE} live startEmpty className="shadow-lg" />
          <p className="mt-3 text-center text-xs text-muted">
            A real trace from one of the demo cases — this is what you watch while it works.
          </p>
        </div>
      </section>

      {/* Pipeline */}
      <section className="border-t border-line bg-surface">
        <div className="mx-auto max-w-6xl px-6 py-16">
          <h2 className="text-xl font-medium tracking-tight text-ink">Three stages, one honest answer</h2>
          <p className="mt-2 max-w-2xl text-sm text-muted">
            Nothing here is a black box. Every stage produces something checkable — a commit hash, a cited diff, a
            before/after number — before the next one starts.
          </p>
          <div className="mt-10 grid grid-cols-1 gap-8 sm:grid-cols-3">
            {STAGES.map((stage) => (
              <div key={stage.n}>
                <span className="font-mono text-xs text-iris-700">{stage.n}</span>
                <h3 className="mt-2 text-base font-medium text-ink">{stage.name}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted">{stage.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Proof: a finished result, styled exactly like the real dashboard */}
      <section className="mx-auto w-full max-w-6xl px-6 py-16">
        <h2 className="text-xl font-medium tracking-tight text-ink">Verified, not vibes</h2>
        <p className="mt-2 max-w-2xl text-sm text-muted">
          A fix is only marked resolved once a sandbox re-run actually recovers the benchmark. If it doesn&apos;t,
          you still get the honest diagnosis — not a fabricated success.
        </p>
        <motion.div
          initial={reduceMotion ? false : { opacity: 0, y: 10 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.4, ease: "easeOut" }}
          className="mt-8 flex flex-wrap items-center gap-2 rounded-md border-l-4 border-resolved bg-resolved/[0.06] px-4 py-3 text-sm text-ink"
        >
          <span>
            <strong className="font-medium">Fix verified.</strong> Benchmark recovered from{" "}
            <span className="font-mono">{formatScore(doneResolvedJob.fix!.before_score)}</span> to{" "}
            <span className="font-mono">{formatScore(doneResolvedJob.fix!.after_score)}</span> (
            <span className="font-mono">
              {formatPctChange(doneResolvedJob.fix!.before_score, doneResolvedJob.fix!.after_score)}
            </span>
            ), confirmed by a sandbox re-run.
          </span>
        </motion.div>
      </section>

      {/* Final CTA */}
      <section className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-col items-start gap-4 px-6 py-16 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-xl font-medium tracking-tight text-ink">Give it a repo.</h2>
            <p className="mt-1.5 text-sm text-muted">A git URL, a benchmark command, and the commit range to search.</p>
          </div>
          <Link
            href="/new"
            className="shrink-0 rounded-md bg-iris px-5 py-2.5 text-sm font-medium text-paper transition-opacity hover:opacity-90"
          >
            Get started
          </Link>
        </div>
      </section>
    </div>
  );
}
