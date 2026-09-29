import { Icon } from "./Icons.jsx";

/** A shape, not a chart: no axis, so it shows direction and nothing else. The number beside it
 *  carries the value. */
function Sparkline({ values, color }) {
  if (values.length < 2) return null;
  const width = 120;
  const height = 36;
  const top = Math.max(...values, 1);
  const x = (index) => (index / (values.length - 1)) * width;
  const y = (value) => height - 2 - (value / top) * (height - 4);
  const line = values.map((value, index) => `${x(index).toFixed(1)},${y(value).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="mb-1 h-9 w-24 shrink-0 self-end" preserveAspectRatio="none" aria-hidden="true">
      <polygon points={`0,${height} ${line} ${width},${height}`} fill={color} fillOpacity="0.15" />
      <polyline points={line} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r="2.2" fill={color} />
    </svg>
  );
}

const ICON_TONES = {
  blue: "bg-series-1/15 text-series-1",
  orange: "bg-series-2/15 text-series-2",
  red: "bg-critical/15 text-critical",
  amber: "bg-warning/15 text-warning",
  grey: "bg-white/10 text-ink-2",
};

/** A change against the previous period, or null while there is no whole period to compare with.
 *  `points` reports a rate as percentage points, because 1.0% to 1.1% is not a 10% change to a
 *  reader. */
export function change(now, before, { higherIsBad, points } = {}) {
  if (before == null || (!points && before === 0)) return null;
  const difference = points ? (now - before) * 100 : ((now - before) / before) * 100;
  return {
    text: points ? `${Math.abs(difference).toFixed(2)} pp` : `${Math.abs(difference).toFixed(1)}%`,
    up: difference >= 0,
    tone: higherIsBad ? (difference > 0 ? "bad" : "good") : "neutral",
  };
}

const DELTA_TONES = { good: "text-good", bad: "text-critical", neutral: "text-ink-2" };

/** `delta` is { text, up, tone } or null. The arrow carries the direction, so the colour is
 *  never the only signal. */
export default function Kpi({ icon, iconTone, label, value, delta, sub, spark, sparkColor, onClick }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={`flex items-start gap-3 rounded-xl border border-white/10 bg-surface p-4 text-left ${
        onClick ? "transition-colors hover:border-white/25" : ""
      }`}
    >
      <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-lg ${ICON_TONES[iconTone]}`}>
        <Icon name={icon} className="h-5 w-5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="whitespace-nowrap text-sm font-medium text-ink-2">{label}</div>
        <div className="mt-1 flex flex-wrap items-baseline gap-x-2">
          <span className="tabular text-3xl font-semibold tracking-tight text-ink">{value}</span>
          {delta && (
            <span className={`tabular text-sm font-medium ${DELTA_TONES[delta.tone]}`}>
              <span aria-hidden="true">{delta.up ? "↑" : "↓"}</span>
              <span className="sr-only">{delta.up ? "up" : "down"}</span> {delta.text}
            </span>
          )}
        </div>
        <div className="mt-1 text-xs text-ink-muted">{sub}</div>
      </div>
      {spark && <Sparkline values={spark} color={sparkColor} />}
    </Tag>
  );
}
