/** The pieces every tab is built from: panels, stat tiles, tables and status pills. */

export function Panel({ title, note, right, children, className = "" }) {
  return (
    <section className={`rounded-lg border border-white/10 bg-surface ${className}`}>
      {(title || right) && (
        <header className="flex items-center gap-3 border-b border-white/10 px-4 py-2.5">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-ink">{title}</h2>
            {note && <p className="truncate text-xs text-ink-muted">{note}</p>}
          </div>
          {right && <div className="ml-auto shrink-0">{right}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

/** A single number with its label. Not a chart, because one value is not a shape. */
export function Stat({ label, value, sub, tone = "ink" }) {
  const tones = { ink: "text-ink", good: "text-good", critical: "text-critical" };
  return (
    <div className="rounded-lg border border-white/10 bg-surface px-4 py-3">
      <div className="text-[11px] font-medium uppercase tracking-wide text-ink-muted">{label}</div>
      <div className={`mt-0.5 text-2xl font-semibold ${tones[tone]}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-ink-muted">{sub}</div>}
    </div>
  );
}

/** Status never travels as colour alone. Every pill carries a glyph and a word. */
export function Pill({ tone, children }) {
  const tones = {
    good: "border-good/40 text-good",
    warning: "border-warning/40 text-warning",
    critical: "border-critical/40 text-critical",
    muted: "border-white/15 text-ink-muted",
  };
  const glyph = { good: "✓", warning: "!", critical: "✕", muted: "·" }[tone];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}
    >
      <span aria-hidden="true">{glyph}</span>
      {children}
    </span>
  );
}

export function Table({ columns, rows, empty = "Nothing yet.", rowKey, rowClass }) {
  if (!rows.length) return <p className="px-4 py-8 text-center text-sm text-ink-muted">{empty}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/10">
            {columns.map((column) => (
              <th
                key={column.key}
                className={`px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-ink-muted ${
                  column.right ? "text-right" : "text-left"
                } ${column.hideSmall ? "hidden md:table-cell" : ""}`}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={rowKey ? rowKey(row) : index}
              className={`border-b border-white/5 last:border-0 ${rowClass ? rowClass(row) : ""}`}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`px-3 py-1.5 ${column.right ? "text-right tabular" : ""} ${
                    column.mono ? "tabular" : ""
                  } ${column.hideSmall ? "hidden md:table-cell" : ""}`}
                >
                  {column.render ? column.render(row) : row[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Button({ children, onClick, tone = "plain", disabled }) {
  const tones = {
    plain: "border-white/15 text-ink hover:border-white/35",
    good: "border-good/40 text-good hover:border-good",
    critical: "border-critical/40 text-critical hover:border-critical",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40 ${tones[tone]}`}
    >
      {children}
    </button>
  );
}
