import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ALERT_STATUS } from "../api.js";

const STATUSES = Object.keys(ALERT_STATUS);

const tooltipStyle = {
  contentStyle: { background: "var(--surface)", border: "1px solid var(--hairline)", borderRadius: 8, fontSize: 12 },
  labelStyle: { color: "var(--ink-2)" },
  itemStyle: { padding: 0 },
};

/** Status is a category here, so every legend names it and every mark carries its colour. */
export function StatusLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2">
      {STATUSES.map((status) => (
        <span key={status} className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: ALERT_STATUS[status].color }} aria-hidden="true" />
          {ALERT_STATUS[status].label}
        </span>
      ))}
    </div>
  );
}

/** One slot for each of the last 24 hours, zero where nothing was raised. */
export function hourlySlots(hourly) {
  const slots = [];
  const now = new Date();
  now.setMinutes(0, 0, 0);
  for (let back = 23; back >= 0; back -= 1) {
    const at = new Date(now.getTime() - back * 3_600_000);
    slots.push({ at: at.getTime(), label: `${String(at.getHours()).padStart(2, "0")}:00`, open: 0, confirmed_fraud: 0, false_positive: 0 });
  }
  const byTime = new Map(slots.map((slot) => [slot.at, slot]));
  for (const row of hourly) {
    const slot = byTime.get(new Date(row.hour).getTime());
    if (slot) slot[row.status] = row.n;
  }
  return slots;
}

/** Alerts raised each hour, stacked by what has happened to them since. */
export function AlertsOverTime({ slots }) {
  if (!slots.some((slot) => slot.open + slot.confirmed_fraud + slot.false_positive)) {
    return <p className="px-4 py-16 text-center text-sm text-ink-muted">No alerts in the last 24 hours.</p>;
  }
  return (
    <div className="px-2 pb-2 pt-3">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={slots} margin={{ top: 4, right: 12, bottom: 0, left: -12 }} barCategoryGap="18%">
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="label"
            stroke="var(--axis)"
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickLine={false}
            interval={1}
            minTickGap={20}
          />
          <YAxis
            stroke="var(--axis)"
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
            width={48}
          />
          <Tooltip cursor={{ fill: "rgb(255 255 255 / 0.05)" }} {...tooltipStyle} />
          {STATUSES.map((status) => (
            <Bar
              key={status}
              dataKey={status}
              name={ALERT_STATUS[status].label}
              stackId="status"
              fill={ALERT_STATUS[status].color}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Three parts of one whole, so a ring reads. The count and share sit beside it in words. */
export function StatusRing({ counts }) {
  const total = STATUSES.reduce((sum, status) => sum + (counts[status] ?? 0), 0);
  if (!total) return <p className="px-4 py-16 text-center text-sm text-ink-muted">No alerts in the last 24 hours.</p>;
  const data = STATUSES.map((status) => ({ status, name: ALERT_STATUS[status].label, value: counts[status] ?? 0 }));

  return (
    <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-3 px-4 py-4">
      <div className="relative h-40 w-40 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              innerRadius="64%"
              outerRadius="100%"
              startAngle={90}
              endAngle={-270}
              stroke="var(--surface)"
              strokeWidth={2}
              isAnimationActive={false}
            >
              {data.map((slice) => (
                <Cell key={slice.status} fill={ALERT_STATUS[slice.status].color} />
              ))}
            </Pie>
            <Tooltip {...tooltipStyle} />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 grid place-content-center text-center">
          <span className="tabular text-3xl font-semibold text-ink">{total.toLocaleString()}</span>
          <span className="text-xs text-ink-muted">alerts</span>
        </div>
      </div>
      <ul className="space-y-2.5 text-sm">
        {data.map((slice) => (
          <li key={slice.status} className="flex items-start gap-2">
            <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: ALERT_STATUS[slice.status].color }} aria-hidden="true" />
            <span>
              <span className="block text-ink">{slice.name}</span>
              <span className="tabular text-xs text-ink-muted">
                {slice.value.toLocaleString()} ({((slice.value / total) * 100).toFixed(1)}%)
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
