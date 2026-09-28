// FILE: frontend/src/lib/theme.tsx
//
// Theme preference is one of three values ("system" | "light" | "dark"),
// persisted in localStorage. The *resolved* theme (always "light" or
// "dark") is what actually lands on <html data-theme>. A blocking inline
// script in the root layout applies the resolved theme before hydration
// (see app/layout.tsx) so there is never a flash of the wrong theme.
//
// Reading the stored preference goes through useSyncExternalStore rather
// than useState+useEffect — same reasoning as useMediaQuery.ts's own
// comment: it's the canonical way to read a browser-only value without
// a synchronous setState-in-effect on mount (which
// eslint-plugin-react-hooks's set-state-in-effect rule flags, and for
// good reason — it's also what causes the classic "flash of default
// theme" bug in the first place). System-theme tracking is delegated to
// the existing useMediaQuery hook rather than reimplemented here.

"use client";

import { createContext, useContext, useEffect, useSyncExternalStore, type ReactNode } from "react";
import { useMediaQuery } from "./useMediaQuery";

export type ThemePreference = "system" | "light" | "dark";
type Resolved = "light" | "dark";

const STORAGE_KEY = "culprit-theme";

/** Minimal external store over localStorage's theme key. A same-tab
 * `localStorage.setItem` doesn't fire the browser's own `storage` event
 * (that only fires in *other* tabs), so this keeps its own listener list
 * and notifies it explicitly from `set` — the store, not a component
 * effect, owns telling React when to re-read. */
const preferenceStore = (() => {
  let listeners: Array<() => void> = [];

  function get(): ThemePreference {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  }
  function getServer(): ThemePreference {
    return "system";
  }
  function subscribe(onChange: () => void) {
    listeners.push(onChange);
    return () => {
      listeners = listeners.filter((l) => l !== onChange);
    };
  }
  function set(pref: ThemePreference) {
    window.localStorage.setItem(STORAGE_KEY, pref);
    listeners.forEach((l) => l());
  }

  return { get, getServer, subscribe, set };
})();

interface ThemeContextValue {
  preference: ThemePreference;
  resolved: Resolved;
  setPreference: (pref: ThemePreference) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const preference = useSyncExternalStore(preferenceStore.subscribe, preferenceStore.get, preferenceStore.getServer);
  const prefersDark = useMediaQuery("(prefers-color-scheme: dark)");
  const resolved: Resolved = preference === "system" ? (prefersDark ? "dark" : "light") : preference;

  // Sanctioned effect shape (per the lint rule's own guidance): purely
  // pushing React's derived value out to an external system (the DOM
  // attribute everything else in globals.css keys off), never a setState
  // call. Redundant with the pre-hydration script on first paint, and
  // that's fine — it's what keeps `system` following the OS afterward.
  useEffect(() => {
    document.documentElement.dataset.theme = resolved;
  }, [resolved]);

  return (
    <ThemeContext.Provider value={{ preference, resolved, setPreference: preferenceStore.set }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within a ThemeProvider");
  return ctx;
}

/** Inline, pre-hydration script source — read by app/layout.tsx and
 * injected via next/script(strategy="beforeInteractive") so the resolved
 * theme is on <html> before first paint. Kept here, next to the rest of
 * the theme logic, rather than duplicated as a string in the layout. */
export const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem("${STORAGE_KEY}");
    var theme =
      stored && stored !== "system"
        ? stored
        : window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
    document.documentElement.setAttribute("data-theme", theme);
  } catch (e) {}
})();
`;
