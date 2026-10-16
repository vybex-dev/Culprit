// FILE: frontend/src/components/dashboard/ProfileEvidence.tsx

import type { Hotspot, Profile } from "@/lib/types";
import { SectionLabel } from "@/components/ui";

function ms(s: number): string {
  const v = s * 1000;
  return v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
}

function CallsCell({ h }: { h: Hotspot }) {
  const ratio = h.calls_before > 0 ? h.calls_after / h.calls_before : null;
  const big = ratio !== null && ratio >= 2;
  return (
    <span className="whitespace-nowrap font-mono tabular-nums">
      <span className="text-muted">{h.calls_before.toLocaleString()}</span>
      <span className="mx-1 text-muted" aria-hidden>
        →
      </span>
      <span className={big || h.calls_before === 0 ? "font-medium text-unresolved" : "text-ink"}>
        {h.calls_after.toLocaleString()}
      </span>
      {big && <span className="ml-1.5 text-[11px] text-unresolved">×{Math.round(ratio as number)}</span>}
      {h.calls_before === 0 && <span className="ml-1.5 text-[11px] text-unresolved">new</span>}
    </span>
  );
}

/** Measured evidence for the diagnosis: which functions got more expensive
 * between the last good commit and the guilty one. Straight from the job's
 * `profile` — nothing here is inferred or estimated. */
export function ProfileEvidence({ profile }: { profile: Profile }) {
  const rows = profile.hotspots.slice(0, 6);
  const maxCum = Math.max(...rows.map((r) => r.cum_after_s), 1e-9);
  return (
    <div className="space-y-3">
      <SectionLabel>Measured evidence</SectionLabel>
      <p className="text-xs leading-relaxed text-muted">
        One profiled run of the benchmark on the last good commit and on the guilty one. These functions grew the most. Profiler
        overhead inflates the times — read the call counts and the relative growth.
      </p>
      <div className="overflow-hidden rounded-xl border border-line bg-surface">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-line text-left text-[11px] text-muted">
              <th className="px-3 py-2 font-normal">Function</th>
              <th className="px-3 py-2 font-normal">Calls</th>
              <th className="px-3 py-2 text-right font-normal">Cumulative (ms)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((h) => (
              <tr key={`${h.func}:${h.line}`}>
                <td className="max-w-[14rem] px-3 py-2">
                  <div className="truncate font-mono text-ink" title={h.file ? `${h.file}:${h.line}` : h.func}>
                    {h.func}
                  </div>
                  {!h.project && <div className="text-[11px] text-muted">library / built-in</div>}
                </td>
                <td className="px-3 py-2">
                  <CallsCell h={h} />
                </td>
                <td className="px-3 py-2 text-right">
                  <div className="font-mono tabular-nums">
                    <span className="text-muted">{ms(h.cum_before_s)}</span>
                    <span className="mx-1 text-muted" aria-hidden>
                      →
                    </span>
                    <span className="text-ink">{ms(h.cum_after_s)}</span>
                  </div>
                  <div className="mt-1 h-1 w-full rounded-full bg-ink/10" aria-hidden>
                    <div className="h-1 rounded-full bg-iris" style={{ width: `${Math.max(3, (h.cum_after_s / maxCum) * 100)}%` }} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
