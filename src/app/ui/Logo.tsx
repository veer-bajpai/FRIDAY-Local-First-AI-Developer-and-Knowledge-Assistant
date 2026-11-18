/** FRIDAY "Spark" mark: a four-point spark in an ink-coloured rounded square.
 *  Colours follow the theme (black on light, white on dark). */
export function LogoMark({ size = 24, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      className={`logo-mark ${className}`}
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label="FRIDAY"
    >
      <rect x="2" y="2" width="60" height="60" rx="17" className="logo-bg" />
      <path
        className="logo-spark"
        d="M32 12c2.2 11.6 8.4 17.8 20 20-11.6 2.2-17.8 8.4-20 20-2.2-11.6-8.4-17.8-20-20 11.6-2.2 17.8-8.4 20-20z"
      />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="logo">
      <LogoMark size={26} />
      <span className="logo-word">FRIDAY</span>
    </span>
  );
}
