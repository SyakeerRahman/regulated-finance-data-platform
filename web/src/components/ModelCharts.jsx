import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const axis = { stroke: "var(--axis)", tick: { fill: "var(--ink-muted)", fontSize: 11 }, tickLine: false };
const ticks = [0, 0.2, 0.4, 0.6, 0.8, 1];
const tooltipStyle = {
  cursor: { stroke: "var(--ink-muted)", strokeWidth: 1 },
  contentStyle: { background: "var(--surface)", border: "1px solid var(--hairline)", borderRadius: 8, fontSize: 12 },
  labelStyle: { color: "var(--ink-2)" },
  itemStyle: { padding: 0 },
};
const fixed = (value) => (value == null ? "-" : Number(value).toFixed(3));

/** Two curves on the unit square, one for each version. Each series brings its own points, so
 *  the x values need not match. `diagonal` draws the line a random guess would make. */
export function CurveChart({ series, xLabel, yLabel, diagonal }) {
  return (
    <div className="px-2 pb-2 pt-3">
      <ResponsiveContainer width="100%" height={250}>
        <LineChart margin={{ top: 8, right: 16, bottom: 18, left: 4 }}>
          <CartesianGrid stroke="var(--grid)" />
          <XAxis
            dataKey="x"
            type="number"
            domain={[0, 1]}
            ticks={ticks}
            {...axis}
            allowDuplicatedCategory={false}
            label={{ value: xLabel, position: "insideBottom", offset: -12, fill: "var(--ink-muted)", fontSize: 11 }}
          />
          <YAxis
            type="number"
            domain={[0, 1]}
            ticks={ticks}
            {...axis}
            axisLine={false}
            width={44}
            label={{ value: yLabel, angle: -90, position: "insideLeft", offset: 12, fill: "var(--ink-muted)", fontSize: 11, dy: 40 }}
          />
          <Tooltip {...tooltipStyle} labelFormatter={(x) => `${xLabel} ${fixed(x)}`} formatter={(value, name) => [fixed(value), name]} />
          {diagonal && (
            <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="var(--ink-muted)" strokeDasharray="4 4" ifOverflow="visible" />
          )}
          {series.map((line) => (
            <Line
              key={line.name}
              data={line.points}
              dataKey="y"
              name={line.name}
              type="linear"
              stroke={line.color}
              strokeWidth={line.width ?? 2}
              strokeDasharray={line.dashed ? "5 4" : undefined}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Precision, recall and F1 as the threshold moves. The dashed line is where the model alerts. */
export function ThresholdChart({ table, threshold }) {
  return (
    <div className="px-2 pb-2 pt-3">
      <ResponsiveContainer width="100%" height={230}>
        <LineChart data={table} margin={{ top: 16, right: 16, bottom: 18, left: 4 }}>
          <CartesianGrid stroke="var(--grid)" />
          <XAxis
            dataKey="threshold"
            type="number"
            domain={[0, 1]}
            ticks={ticks}
            {...axis}
            label={{ value: "Threshold", position: "insideBottom", offset: -12, fill: "var(--ink-muted)", fontSize: 11 }}
          />
          <YAxis type="number" domain={[0, 1]} ticks={ticks} {...axis} axisLine={false} width={44} />
          <Tooltip {...tooltipStyle} labelFormatter={(x) => `Threshold ${Number(x).toFixed(2)}`} formatter={(value, name) => [fixed(value), name]} />
          <Line dataKey="precision" name="Precision" stroke="var(--series-1)" strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
          <Line dataKey="recall" name="Recall" stroke="var(--series-2)" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="f1" name="F1" stroke="var(--ink-2)" strokeWidth={1.6} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
          {threshold != null && (
            <ReferenceLine
              x={threshold}
              stroke="var(--critical)"
              strokeDasharray="4 3"
              label={{ value: threshold.toFixed(3), position: "top", fill: "var(--critical)", fontSize: 11 }}
            />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** A labelled bar for each value, on one shared scale. */
export function ValueBars({ rows, format, color = "var(--series-1)", badge }) {
  const largest = Math.max(...rows.map((row) => Math.abs(row.value)), 1e-9);
  return (
    <ul className="space-y-2.5 px-4 py-4">
      {rows.map((row) => (
        <li key={row.label} className="grid grid-cols-[minmax(7rem,9rem)_1fr_auto] items-center gap-3 text-sm">
          <span className="truncate text-ink-2" title={row.label}>
            {row.label}
          </span>
          <span className="h-3 overflow-hidden rounded-sm bg-white/5" aria-hidden="true">
            <span className="block h-full rounded-sm" style={{ width: `${Math.max((Math.abs(row.value) / largest) * 100, 2)}%`, background: row.color ?? color }} />
          </span>
          <span className="tabular flex items-center gap-2 whitespace-nowrap text-xs text-ink-2">
            {format(row.value)}
            {badge?.(row)}
          </span>
        </li>
      ))}
    </ul>
  );
}
