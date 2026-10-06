// FILE: frontend/src/app/layout.tsx
//
// Fonts are self-hosted via @fontsource-variable (npm packages, no runtime
// call to fonts.googleapis.com) rather than next/font/google. That's a
// deliberate choice, not a workaround for this sandbox's network rules:
// self-hosting avoids a third-party font request on every real page load
// too. See the hand-back notes for the full reasoning.
//
// Theme: a tiny inline script (Script, strategy="beforeInteractive") sets
// data-theme on <html> before first paint, so there's no flash of the
// wrong theme while React hydrates — see lib/theme.tsx for the full
// system (defaults to the OS theme, with a manual override that persists).

import type { Metadata } from "next";
import Link from "next/link";
import Script from "next/script";
import "./globals.css";
import { ThemeProvider, THEME_INIT_SCRIPT } from "@/lib/theme";
import { ThemeToggle } from "@/components/ThemeToggle";
import { HeaderLogo } from "@/components/HeaderLogo";
import { ModeBanner } from "@/components/ModeBanner";
import { Footer } from "@/components/Footer";

export const metadata: Metadata = {
  title: "Culprit — Performance Regression Detective",
  description:
    "Finds the exact commit where a benchmark got slower, explains why, and proves the fix.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased" suppressHydrationWarning>
      <body className="min-h-full flex flex-col">
        <Script id="theme-init" strategy="beforeInteractive">
          {THEME_INIT_SCRIPT}
        </Script>
        <ThemeProvider>
          <header className="border-b border-line bg-paper/95 backdrop-blur sticky top-0 z-20">
            <div className="mx-auto max-w-7xl px-4 sm:px-6 py-3 flex items-center justify-between gap-3">
              <Link href="/" className="flex items-center gap-2.5 group">
                <HeaderLogo size={28} />
                <span className="font-mono text-[15px] font-medium tracking-tight text-ink">
                  culprit
                </span>
              </Link>
              <div className="flex items-center gap-3 sm:gap-5">
                <nav className="flex items-center gap-5 whitespace-nowrap text-sm text-muted">
                  <Link href="/new" className="hover:text-ink transition-colors">
                    New analysis
                  </Link>
                  <Link href="/jobs" className="hover:text-ink transition-colors">
                    History
                  </Link>
                  <Link href="/dev/states" className="hidden sm:inline hover:text-ink transition-colors">
                    State reference
                  </Link>
                </nav>
                <ThemeToggle />
              </div>
            </div>
          </header>
          <ModeBanner />
          <main className="flex-1 flex flex-col">{children}</main>
          <Footer />
        </ThemeProvider>
      </body>
    </html>
  );
}
