import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** How many payments got each score, since the service started.
 *
 * Log scale, and the axis says so. About 99% of payments score near 0, so on a linear scale every
 * bar past the first is one pixel high and the alerts near 1 cannot be seen at all.
 */
export default function ScoreHistogram({ histogram, threshold }) {
  const total = histogram.reduce((sum, count) => sum + count, 0);
  if (!total) return <p className="px-4 py-16 text-center text-sm text-ink-muted">Press Start. Scores collect here.</p>;

  const width = 1 / histogram.length;
  const largest = Math.max(...histogram);
  const ticks = [1, 10, 100, 1_000, 10_000, 100_000, 1_000_000].filter((tick) => tick <= largest * 10);
  const data = histogram.map((count, index) => {
    const mid = (index + 0.5) * width;
    // An empty bin has no bar. Zero has no place on a log axis.
    return { mid, count: count || null, alert: threshold != null && mid >= threshold };
  });

  return (
    <div className="px-2 pb-2 pt-3">
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 16, right: 16, bottom: 0, left: -8 }} barCategoryGap={1}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="mid"
            type="number"
            domain={[0, 1]}
            ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]}
            tickFormatter={(value) => value.toFixed(1)}
            stroke="var(--axis)"
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickLine={false}
          />
          <YAxis
            scale="log"
            domain={[0.8, ticks[ticks.length - 1]]}
            ticks={ticks}
            allowDataOverflow
            stroke="var(--axis)"
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(value) => Math.round(value).toLocaleString()}
            width={48}
          />
          <Tooltip
            cursor={{ fill: "rgb(255 255 255 / 0.05)" }}
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--hairline)",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "var(--ink-2)" }}
            labelFormatter={(mid) => `score ${(mid - width / 2).toFixed(2)} to ${(mid + width / 2).toFixed(2)}`}
            formatter={(value) => [value?.toLocaleString() ?? 0, "payments"]}
          />
          <Bar dataKey="count" isAnimationActive={false}>
            {data.map((bin) => (
              <Cell key={bin.mid} fill={bin.alert ? "var(--series-2)" : "var(--series-1)"} />
            ))}
          </Bar>
          {threshold != null && (
            <ReferenceLine
              x={threshold}
              stroke="var(--critical)"
              strokeDasharray="4 3"
              label={{ value: `threshold ${threshold.toFixed(3)}`, position: "insideTopRight", fill: "var(--critical)", fontSize: 11, dy: -14 }}
            />
          )}
        </BarChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap items-center justify-center gap-4 pb-1 text-xs text-ink-2">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-series-1" aria-hidden="true" />
          Pass
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-series-2" aria-hidden="true" />
          Alert
        </span>
        <span className="text-ink-muted">Count, log scale</span>
      </div>
    </div>
  );
}
