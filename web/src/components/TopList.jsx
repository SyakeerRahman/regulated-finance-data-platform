/** A ranked list with a bar for each share. Bars, not a donut: five lengths on one baseline are
 *  easy to compare, and five angles are not. */
export default function TopList({ rows, total, label, badge, empty }) {
  if (!rows.length) return <p className="px-4 py-16 text-center text-sm text-ink-muted">{empty}</p>;
  return (
    <ul className="space-y-3 px-4 py-4">
      {rows.map(([key, count]) => {
        const share = total ? count / total : 0;
        return (
          <li key={key} className="grid grid-cols-[minmax(6.5rem,auto)_1fr_auto] items-center gap-3 text-sm">
            <span className="flex min-w-0 items-center gap-2 text-ink-2">
              {badge && (
                <span className="tabular w-8 shrink-0 rounded border border-white/15 py-px text-center text-[11px] font-semibold text-ink">
                  {key}
                </span>
              )}
              <span className="truncate">{label(key)}</span>
            </span>
            <span className="h-3.5 overflow-hidden rounded-sm bg-white/5" aria-hidden="true">
              <span className="block h-full rounded-sm bg-series-2" style={{ width: `${Math.max(share * 100, 2)}%` }} />
            </span>
            <span className="tabular whitespace-nowrap text-ink-2">
              {count} <span className="text-ink-muted">({(share * 100).toFixed(1)}%)</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
