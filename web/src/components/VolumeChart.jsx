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

/** Transactions and alerts per 10 seconds, over the last few minutes.
 *
 * One y-axis. Alerts are a subset of transactions, so they share the scale and the comparison
 * is the point of the chart. A second axis would make a rate of 1% look like a rate of 50%.
 */
export default function VolumeChart({ buckets }) {
  if (buckets.length < 2) {
    return (
      <p className="px-4 py-12 text-center text-sm text-ink-muted">
        Press Start. The chart draws once two intervals have passed.
      </p>
    );
  }

  return (
    <div className="px-2 pb-2 pt-4">
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={buckets} margin={{ top: 4, right: 12, bottom: 0, left: -18 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="label"
            stroke="var(--axis)"
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickLine={false}
            minTickGap={40}
          />
          <YAxis
            stroke="var(--axis)"
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
            width={44}
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
            name="transactions"
            stroke="var(--series-1)"
            strokeWidth={2}
            fill="var(--series-1)"
            fillOpacity={0.14}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="alerts"
            name="alerts"
            stroke="var(--series-2)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>

      {/* Two series, so a legend is always present. Identity never rests on colour alone. */}
      <div className="flex items-center justify-center gap-5 pb-1 text-xs text-ink-2">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded-full bg-series-1" aria-hidden="true" />
          transactions
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded-full bg-series-2" aria-hidden="true" />
          alerts
        </span>
      </div>
    </div>
  );
}
