// Route-level loading UI: Next.js shows this automatically while a page suspends.
import { LogoLoader } from "@/components/LogoLoader";

export default function Loading() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 py-24">
      <LogoLoader size={72} />
      <p className="font-mono text-xs tracking-wide text-muted">loading</p>
    </div>
  );
}
