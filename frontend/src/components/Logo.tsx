// Culprit logo mark. Inline SVG that reads the app's theme tokens, so it
// switches with the light/dark toggle automatically (no per-theme files).
export function LogoMark({
  size = 28,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 512 512"
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label="Culprit"
      fill="none"
    >
      <g stroke="var(--iris)" strokeWidth={36} strokeLinecap="round">
        <path d="M413.67 358.39 A188 188 0 1 1 413.67 153.61" />
        <path d="M156.71 178.43 A126 126 0 1 1 156.71 333.57" />
        <path d="M300.46 302.04 A64 64 0 1 1 300.46 209.96" />
      </g>
      <circle cx="256" cy="256" r="24" fill="var(--butter)" />
    </svg>
  );
}
