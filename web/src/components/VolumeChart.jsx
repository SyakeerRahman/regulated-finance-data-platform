import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/** Transactions and alerts per 10 seconds, over the last hour. The service keeps the counts, so
 *  a page reload does not empty the chart.
 *
 * One y-axis. Alerts are a subset of transactions, so they share the scale and the comparison
 * is the point of the chart. A second axis would make a rate of 1% look like a rate of 50%.
 */
export default function VolumeChart({ buckets }) {
  if (buckets.length < 2) {
    return (
      <p className="px-4 py-16 text-center text-sm text-ink-muted">
        Press Start. The chart draws once two 10-second intervals have passed.
      </p>
    );
  }

  return (
    <div className="px-2 pb-2 pt-3">
      <ResponsiveContainer width="100%" height={230}>
        <ComposedChart data={buckets} margin={{ top: 4, right: 12, bottom: 0, left: -12 }}>
          <defs>
            <linearGradient id="volume-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--series-1)" stopOpacity={0.35} />
              <stop offset="100%" stopColor="var(--series-1)" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="label"
            stroke="var(--axis)"
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickLine={false}
            minTickGap={48}
          />
          <YAxis
            stroke="var(--axis)"
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
            width={48}
          />
          <Tooltip
            cursor={{ stroke: "var(--ink-muted)", strokeWidth: 1 }}
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--hairline)",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "var(--ink-2)" }}
            itemStyle={{ padding: 0 }}
          />
          <Area
            type="monotone"
            dataKey="scored"
            name="All transactions"
            stroke="var(--series-1)"
            strokeWidth={1.8}
            fill="url(#volume-fill)"
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="alerts"
            name="Alerts"
            stroke="var(--series-2)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Two series, so a legend is always present. Identity never rests on colour alone. */
export function VolumeLegend() {
  return (
    <div className="flex items-center gap-4 text-xs text-ink-2">
      <span className="inline-flex items-center gap-1.5">
        <span className="h-2.5 w-2.5 rounded-full bg-series-1" aria-hidden="true" />
        All transactions (count)
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="h-2.5 w-2.5 rounded-full bg-series-2" aria-hidden="true" />
        Alerts (count)
      </span>
    </div>
  );
}
