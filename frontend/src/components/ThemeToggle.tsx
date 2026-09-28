// FILE: frontend/src/components/ThemeToggle.tsx
//
// Three-way segmented control: System / Light / Dark. Defaults to
// System (see lib/theme.tsx) with a manual override that persists.

"use client";

import { motion } from "motion/react";
import { useTheme, type ThemePreference } from "@/lib/theme";

const OPTIONS: { value: ThemePreference; label: string }[] = [
  { value: "system", label: "Auto" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

function SunIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden>
      <circle cx="8" cy="8" r="3.2" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M8 1.2v1.6M8 13.2v1.6M14.8 8h-1.6M2.8 8H1.2M12.7 3.3l-1.1 1.1M4.4 11.6l-1.1 1.1M12.7 12.7l-1.1-1.1M4.4 4.4 3.3 3.3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M13.2 9.6A5.6 5.6 0 1 1 6.4 2.8a4.6 4.6 0 0 0 6.8 6.8Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function AutoIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden>
      <rect x="1.5" y="2.5" width="13" height="8.5" rx="1.4" stroke="currentColor" strokeWidth="1.4" />
      <path d="M5.5 14h5M8 11v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

const ICON: Record<ThemePreference, () => React.ReactElement> = {
  system: AutoIcon,
  light: SunIcon,
  dark: MoonIcon,
};

export function ThemeToggle() {
  const { preference, setPreference } = useTheme();

  return (
    <div
      role="radiogroup"
      aria-label="Theme"
      className="relative inline-flex items-center gap-0.5 rounded-full border border-line bg-surface-2 p-0.5"
    >
      {OPTIONS.map((opt) => {
        const Icon = ICON[opt.value];
        const active = preference === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={opt.label}
            onClick={() => setPreference(opt.value)}
            className="relative z-10 flex h-6 w-7 items-center justify-center rounded-full text-muted transition-colors"
          >
            {active && (
              <motion.span
                layoutId="theme-toggle-active"
                className="absolute inset-0 rounded-full bg-iris"
                transition={{ type: "spring", stiffness: 500, damping: 34 }}
              />
            )}
            <span className={active ? "relative text-paper" : "relative hover:text-ink"}>
              <Icon />
            </span>
            <span className="sr-only">{opt.label}</span>
          </button>
        );
      })}
    </div>
  );
}
