// FILE: frontend/src/app/layout.tsx — place at this path in the Culprit repo
//
// Fonts are self-hosted via @fontsource-variable (npm packages, no runtime
// call to fonts.googleapis.com) rather than next/font/google. That's a
// deliberate choice, not a workaround for this sandbox's network rules:
// self-hosting avoids a third-party font request on every real page load
// too. See the hand-back notes for the full reasoning.

import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Culprit — Performance Regression Detective",
  description:
    "Finds the exact commit where a benchmark got slower, explains why, and proves the fix.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <header className="border-b border-line bg-paper/95 backdrop-blur sticky top-0 z-20">
          <div className="mx-auto max-w-6xl px-6 py-3 flex items-center justify-between">
            <Link href="/" className="flex items-center gap-2 group">
              <span
                aria-hidden
                className="inline-block h-2 w-2 rounded-full bg-iris group-hover:bg-butter-700 transition-colors"
              />
              <span className="font-mono text-[15px] font-medium tracking-tight text-ink">
                culprit
              </span>
            </Link>
            <nav className="flex items-center gap-5 text-sm text-muted">
              <Link href="/" className="hover:text-ink transition-colors">
                New analysis
              </Link>
              <Link href="/dev/states" className="hover:text-ink transition-colors">
                State reference
              </Link>
            </nav>
          </div>
        </header>
        <main className="flex-1 flex flex-col">{children}</main>
      </body>
    </html>
  );
}
