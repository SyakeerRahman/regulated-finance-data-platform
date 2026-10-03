import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const axis = {
  stroke: "var(--axis)",
  tick: { fill: "var(--ink-muted)", fontSize: 11 },
  tickLine: false,
};
const tooltipStyle = {
  cursor: { stroke: "var(--ink-muted)", strokeWidth: 1 },
  contentStyle: { background: "var(--surface)", border: "1px solid var(--hairline)", borderRadius: 8, fontSize: 12 },
  labelStyle: { color: "var(--ink-2)" },
  itemStyle: { padding: 0 },
};

function Empty({ children }) {
  return <p className="px-4 py-14 text-center text-sm text-ink-muted">{children}</p>;
}

export function Legend({ items }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2">
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5">
          <svg width="16" height="6" aria-hidden="true">
            <line x1="0" y1="3" x2="16" y2="3" stroke={item.color} strokeWidth="2.5" strokeDasharray={item.dashed ? "4 3" : undefined} />
          </svg>
          {item.label}
        </span>
      ))}
    </div>
  );
}

/** Rows scored and rows written to bronze, added up over the hour. The gap between the two lines
 *  is the buffer: rows scored and shown, not yet in the lake. The write line steps, because the
 *  service writes 2,000 rows or one minute at a time. */
export function VolumeChart({ points }) {
  if (points.length < 2) return <Empty>Press Start. The chart draws once two 10-second intervals have passed.</Empty>;
  return (
    <div className="px-2 pb-2 pt-3">
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={points} margin={{ top: 4, right: 12, bottom: 0, left: -6 }}>
          <defs>
            <linearGradient id="ingested-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--series-1)" stopOpacity={0.3} />
              <stop offset="100%" stopColor="var(--series-1)" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="label" {...axis} minTickGap={48} />
          <YAxis {...axis} axisLine={false} width={54} tickFormatter={(value) => value.toLocaleString()} />
          <Tooltip {...tooltipStyle} formatter={(value) => value.toLocaleString()} />
          <Area type="monotone" dataKey="ingested" name="Ingested" stroke="var(--series-1)" strokeWidth={1.8} fill="url(#ingested-fill)" dot={false} isAnimationActive={false} />
          <Line type="stepAfter" dataKey="written" name="Written to lake" stroke="var(--series-2)" strokeWidth={2} dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Scoring time for each 10 seconds. A gap is a stretch with nothing scored, not a fast one. */
export function LatencyChart({ points }) {
  if (!points.some((point) => point.p95 != null)) return <Empty>No payments scored yet.</Empty>;
  return (
    <div className="px-2 pb-2 pt-3">
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={points} margin={{ top: 4, right: 12, bottom: 0, left: -6 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="label" {...axis} minTickGap={48} />
          <YAxis {...axis} axisLine={false} width={54} tickFormatter={(value) => `${value} ms`} />
          <Tooltip {...tooltipStyle} formatter={(value) => (value == null ? "-" : `${value.toFixed(1)} ms`)} />
          <Line type="monotone" dataKey="p50" name="p50" stroke="var(--series-1)" strokeWidth={1.8} dot={false} isAnimationActive={false} />
          <Line type="monotone" dataKey="p95" name="p95" stroke="var(--series-2)" strokeWidth={1.8} dot={false} isAnimationActive={false} />
          <Line type="monotone" dataKey="p99" name="p99" stroke="var(--series-2)" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
