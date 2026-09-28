// FILE: frontend/src/app/page.tsx
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
import { AgentCursor, type CursorWaypoint } from "@/components/dashboard/AgentCursor";
import { BisectIcon, DiagnoseIcon, FixIcon } from "@/components/dashboard/MissionControl";
import { LiveDot } from "@/components/ui";

const HERO_TRACE = buildTrace(doneResolvedJob).slice(0, 6);
const REGRESSED = doneResolvedJob.timeline.find((e) => e.commit === doneResolvedJob.regression_commit)!;
const BASELINE = doneResolvedJob.timeline[doneResolvedJob.timeline.indexOf(REGRESSED) - 1];
const STREAM_MS_PER_LINE = 130; // must track TraceFeed's own pump interval

const STAGES = [
  {
    n: "01",
    name: "Bisect",
    Icon: BisectIcon,
    body: "Binary-searches the commit history in isolated sandboxes until it lands on the exact commit where the benchmark got worse.",
  },
  {
    n: "02",
    name: "Diagnose",
    Icon: DiagnoseIcon,
    body: "Reads the guilty diff in full file context and names the root cause — N+1 queries, a lost cache, an O(n²) creeping in — citing the exact lines responsible.",
  },
  {
    n: "03",
    name: "Fix & verify",
    Icon: FixIcon,
    body: 'Proposes a patch, applies it in a fresh sandbox, and re-runs the benchmark. Nothing is called "fixed" until a real re-run proves it.',
  },
] as const;

function useLoopedHeroTrace() {
  const [cycle, setCycle] = useState(0);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    if (reduceMotion) return;
    const totalMs = 1100 + HERO_TRACE.length * STREAM_MS_PER_LINE + 3400;
    const t = setTimeout(() => setCycle((c) => c + 1), totalMs);
    return () => clearTimeout(t);
  }, [cycle, reduceMotion]);

  return cycle;
}

/** Three waypoints across the hero trace card, cycling on their own
 * timer — a lighter-weight cousin of Mission Control's job-state-driven
 * cursor, since here there's no real job to key off of, just the same
 * fixture looping for demonstration. Labels still come from real trace
 * text, never invented copy. */
function useHeroCursorWaypoint(cycle: number): CursorWaypoint {
  const [step, setStep] = useState(0);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    if (reduceMotion) return;
    const id = setInterval(() => setStep((s) => (s + 1) % 3), 1500);
    return () => clearInterval(id);
  }, [reduceMotion]);

  const points: CursorWaypoint[] = [
    { id: `${cycle}-0`, xPct: 50, yPct: 14, label: HERO_TRACE[0].text },
    { id: `${cycle}-1`, xPct: 50, yPct: 52, label: HERO_TRACE[3]?.text ?? HERO_TRACE[0].text },
    { id: `${cycle}-2`, xPct: 50, yPct: 88, label: HERO_TRACE[HERO_TRACE.length - 1].text },
  ];
  return points[step];
}

export default function LandingPage() {
  const cycle = useLoopedHeroTrace();
  const cursorWaypoint = useHeroCursorWaypoint(cycle);
  const reduceMotion = useReducedMotion();

  return (
    <div className="flex flex-1 flex-col">
      {/* Hero */}
      <section className="relative overflow-hidden">
        {!reduceMotion && (
          <div
            className="aurora-drift pointer-events-none absolute -inset-32 opacity-50"
            style={{
              background:
                "radial-gradient(30% 40% at 15% 20%, color-mix(in srgb, var(--iris) 22%, transparent), transparent 70%), radial-gradient(26% 34% at 85% 60%, color-mix(in srgb, var(--butter) 18%, transparent), transparent 70%)",
            }}
            aria-hidden
          />
        )}
        <div className="relative mx-auto grid w-full max-w-6xl grid-cols-1 items-center gap-14 px-6 pb-20 pt-16 lg:grid-cols-[minmax(0,1fr)_460px] lg:pt-24">
          <div className="max-w-xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-line bg-surface-2 px-2.5 py-1">
              <LiveDot variant="butter" live />
              <span className="font-mono text-[11px] tracking-wide text-iris-700">Watches its own work, live</span>
            </div>
            <h1 className="mt-4 text-4xl font-semibold leading-[1.06] tracking-tight text-ink sm:text-5xl">
              Finds the exact commit that made your app slower.
            </h1>
            <p className="mt-5 text-base leading-relaxed text-muted">
              Point it at a repo and a benchmark. It bisects the history, explains the regression in plain English
              with the lines to prove it, and verifies a fix in a sandbox before it tells you it&apos;s done.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-4">
              <Link
                href="/new"
                className="glow-iris rounded-md bg-iris px-5 py-2.5 text-sm font-medium text-paper transition-opacity hover:opacity-90"
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
            <dl className="mt-12 grid max-w-md grid-cols-3 gap-5 border-t border-line pt-6">
              {[
                { dt: "Regression found at", dd: shortSha(REGRESSED.commit), accent: "bg-iris" },
                { dt: "Benchmark", dd: `${formatScore(BASELINE.score)} → ${formatScore(REGRESSED.score)}`, accent: "bg-butter-700" },
                { dt: "Change", dd: formatPctChange(BASELINE.score, REGRESSED.score), accent: "bg-unresolved", text: "text-unresolved" },
              ].map((stat) => (
                <div key={stat.dt}>
                  <span className={`mb-2 block h-0.5 w-5 rounded-full ${stat.accent}`} aria-hidden />
                  <dt className="text-xs text-muted">{stat.dt}</dt>
                  <dd className={`mt-1 font-mono text-sm ${stat.text ?? "text-ink"}`}>{stat.dd}</dd>
                </div>
              ))}
            </dl>
          </div>

          <div>
            {/* The cursor's percentages are measured against this wrapper, so it
                holds only the trace card — not the caption below it. */}
            <div className="relative">
              <TraceFeed
                key={cycle}
                events={HERO_TRACE}
                live
                startEmpty
                controls={false}
                announce={false}
                heightClassName="h-64"
                className="shadow-lg"
              />
              <AgentCursor waypoint={cursorWaypoint} />
            </div>
            <p className="mt-3 text-center text-xs text-muted">
              A real trace from one of the demo cases — this is what you watch while it works.
            </p>
          </div>
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
                <div className="flex items-center gap-2 text-iris-700">
                  <stage.Icon />
                  <span className="font-mono text-xs">{stage.n}</span>
                </div>
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
          className="glow-resolved mt-8 flex flex-wrap items-center gap-2 rounded-xl border-l-4 border-resolved bg-resolved/[0.08] px-4 py-3 text-sm text-ink"
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
            className="glow-iris shrink-0 rounded-md bg-iris px-5 py-2.5 text-sm font-medium text-paper transition-opacity hover:opacity-90"
          >
            Get started
          </Link>
        </div>
      </section>
    </div>
  );
}
