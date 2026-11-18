/** Lightweight SVG charts (no chart library). Colours come from CSS variables. */

function points(values: number[], width: number, height: number, pad = 3) {
  const top = Math.max(1, ...values) * 1.15;
  const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0;
  return values.map((value, index) => [
    pad + index * step,
    height - pad - (value / top) * (height - pad * 2),
  ]);
}

export function Sparkline({ values, width = 92, height = 30 }: { values: number[]; width?: number; height?: number }) {
  if (values.length < 2) return null;
  const path = points(values, width, height).map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={path} />
    </svg>
  );
}

export function AreaChart({ values, labels, height = 190 }: { values: number[]; labels: string[]; height?: number }) {
  const width = 600;
  const pts = points(values, width, height, 6);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  const fill = `${line} L${last[0].toFixed(1)},${height} L${pts[0][0].toFixed(1)},${height} Z`;
  const max = Math.max(...values);
  // at most ~6 evenly spaced date labels, always including the last day
  const every = Math.max(1, Math.ceil(labels.length / 6));
  const shown = labels.map((label, index) =>
    (labels.length - 1 - index) % every === 0 ? label : "");
  return (
    <div className="area-wrap">
      <svg className="area" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img"
        aria-label={`Activity over ${values.length} days, peak ${max} per day`}>
        <defs>
          <linearGradient id="area-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" className="area-stop-top" />
            <stop offset="1" className="area-stop-bottom" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((ratio) => (
          <line key={ratio} x1="0" x2={width} y1={height * ratio} y2={height * ratio} className="area-grid" vectorEffect="non-scaling-stroke" />
        ))}
        <path d={fill} fill="url(#area-fill)" />
        <path d={line} className="area-line" vectorEffect="non-scaling-stroke" />
      </svg>
      <span className="area-point" style={{ left: `${(last[0] / width) * 100}%`, top: `${(last[1] / height) * 100}%` }} />
      <div className="area-labels" aria-hidden="true">
        {shown.map((label, index) => <span key={index}>{label}</span>)}
      </div>
    </div>
  );
}

export const DONUT_COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)"];

export function Donut({ parts, size = 120, label }: { parts: { name: string; value: number }[]; size?: number; label: string }) {
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  const radius = size / 2 - 10;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  return (
    <svg className="donut" width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label}>
      <circle cx={size / 2} cy={size / 2} r={radius} className="donut-track" />
      {total > 0 &&
        parts.map((part, index) => {
          const length = (part.value / total) * circumference;
          const gap = parts.length > 1 ? 3 : 0;
          const segment = (
            <circle key={part.name} cx={size / 2} cy={size / 2} r={radius} fill="none"
              stroke={DONUT_COLORS[index % DONUT_COLORS.length]} strokeWidth="14" strokeLinecap="round"
              strokeDasharray={`${Math.max(0, length - gap)} ${circumference}`} strokeDashoffset={-offset}
              transform={`rotate(-90 ${size / 2} ${size / 2})`} />
          );
          offset += length;
          return segment;
        })}
      <text x="50%" y="48%" textAnchor="middle" className="donut-num">{total}</text>
      <text x="50%" y="63%" textAnchor="middle" className="donut-lbl">files</text>
    </svg>
  );
}
