// Site footer: brand, basic links, and the "Powered by VYBEX" tag.
// The VYBEX wordmark's animated gradient lives in globals.css (.pbv-wordmark).

import Link from "next/link";
import { LogoMark } from "@/components/Logo";

export function Footer() {
  return (
    <footer className="border-t border-line bg-paper">
      <div className="mx-auto grid max-w-7xl grid-cols-1 items-center justify-items-center gap-4 px-4 py-6 text-center text-[13px] text-muted sm:px-6 md:grid-cols-[1fr_auto_1fr] md:justify-items-stretch md:text-left">
        <div className="flex items-center justify-center gap-2.5 md:justify-self-start">
          <LogoMark size={18} />
          <span>
            <span className="font-mono font-medium text-ink">culprit</span> — performance regression detective
          </span>
        </div>

        <nav
          aria-label="Footer"
          className="flex flex-wrap items-center justify-center gap-x-5 gap-y-2 whitespace-nowrap md:justify-self-center"
        >
          <Link href="/new" className="transition-colors hover:text-ink">
            New analysis
          </Link>
          <Link href="/jobs" className="transition-colors hover:text-ink">
            History
          </Link>
          <Link href="/dev/states" className="transition-colors hover:text-ink">
            State reference
          </Link>
          <a
            href="https://github.com/vybex-dev/Culprit"
            target="_blank"
            rel="noopener noreferrer"
            className="transition-colors hover:text-ink"
          >
            GitHub
          </a>
        </nav>

        <a
          className="powered-by-vybex whitespace-nowrap md:justify-self-end"
          href="https://vybex-dev.vercel.app"
          target="_blank"
          rel="noopener noreferrer"
        >
          Powered by <span className="pbv-wordmark">VYBEX</span>
        </a>
      </div>
    </footer>
  );
}
