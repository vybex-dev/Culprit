// FILE: frontend/src/components/dashboard/GuardTestCard.tsx
//
// The regression test for the fix. It is shown as a deliverable ONLY when the
// sandbox proved it (red on the regressed commit, green with the fix) — straight
// from job.guard_test, never inferred. When no test could be proven, that is said
// plainly with the reason, and nothing is offered.

"use client";

import { useState } from "react";
import clsx from "clsx";
import { guardTestUrl } from "@/lib/api";
import type { GuardTest } from "@/lib/types";
import { Pill, SectionLabel } from "@/components/ui";

function Proof({ ok, children, title }: { ok: boolean | null | undefined; children: React.ReactNode; title?: string }) {
  if (ok === null || ok === undefined) return null;
  return (
    <li className="flex items-start gap-2" title={title}>
      <span className={clsx("mt-px font-mono text-xs", ok ? "text-resolved" : "text-unresolved")} aria-hidden>
        {ok ? "✓" : "✗"}
      </span>
      <span className="text-sm text-ink">{children}</span>
    </li>
  );
}

export function GuardTestCard({ jobId, guardTest: g }: { jobId: string; guardTest: GuardTest }) {
  const [copied, setCopied] = useState(false);
  const verified = g.status === "verified" && Boolean(g.test_code);

  async function copy() {
    try {
      await navigator.clipboard.writeText(g.test_code ?? "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — the download button still works */
    }
  }

  if (!verified) {
    return (
      <div className="space-y-2">
        <SectionLabel>Regression test</SectionLabel>
        <p className="text-sm leading-relaxed text-muted">
          No regression test was shipped. A test is offered only when it is proven to fail on the regressed code and pass with the fix
          {g.reason ? <> — {g.reason}</> : "."}
        </p>
        {g.attempts.length > 0 && (
          <details className="text-xs text-muted">
            <summary className="cursor-pointer">what was tried</summary>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              {g.attempts.map((a) => (
                <li key={a.attempt}>
                  attempt {a.attempt}: {a.status.replace(/_/g, " ")}
                  {a.note ? ` — ${a.note}` : ""}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <SectionLabel>Regression test</SectionLabel>
        <Pill variant="resolved">Proven in sandbox</Pill>
      </div>
      {g.asserts && <p className="text-sm leading-relaxed text-ink">{g.asserts}</p>}
      <ul className="space-y-1.5 rounded-xl border border-line bg-surface px-3 py-2.5">
        <Proof ok={g.fails_without_fix} title="Run on the regressed commit: an assertion failed (not a crash).">
          Fails on the regressed commit
        </Proof>
        <Proof ok={g.passes_with_fix} title="Run on the regressed commit with the verified fix applied.">
          Passes with the fix applied
        </Proof>
        <Proof
          ok={g.passes_on_good}
          title="Extra evidence, not a gate: a test that is also green before the regression encodes the intended behaviour."
        >
          {g.passes_on_good === false ? "Not green on the last good commit — may be tied to the fix's exact shape" : "Also green on the last good commit"}
        </Proof>
      </ul>
      <p className="text-xs leading-relaxed text-muted">
        Save it as <code className="font-mono text-ink">{g.file_name}</code> in the repository root and run{" "}
        <code className="font-mono text-ink">{g.command}</code>. It asserts a deterministic count, never wall-clock time, so it will not flake.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <a
          href={guardTestUrl(jobId)}
          download
          className="inline-flex items-center rounded-md border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-line-strong hover:bg-surface-2"
        >
          Download {g.file_name}
        </a>
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center rounded-md border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-line-strong hover:bg-surface-2"
        >
          {copied ? "Copied ✓" : "Copy code"}
        </button>
      </div>
      <details className="rounded-xl border border-line bg-surface">
        <summary className="cursor-pointer px-3 py-2 text-xs text-muted">View the test</summary>
        <pre className="max-h-80 overflow-auto border-t border-line p-3 font-mono text-xs leading-relaxed text-ink">{g.test_code}</pre>
      </details>
    </div>
  );
}
