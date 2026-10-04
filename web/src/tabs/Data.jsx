import { useCallback, useEffect, useState } from "react";
import { Panel, Pill } from "../components/Chrome.jsx";
import { Icon } from "../components/Icons.jsx";
import { ALERT_STATUS, age, bytes, getLakeRows, getLakeTables, getTrace, money, reasonLabel } from "../api.js";

// The medallion layers, in the order a payment moves through them.
const LAYERS = [
  { name: "Bronze", note: "As received", tables: ["bronze/transactions"] },
  { name: "Silver", note: "Cleaned and judged", tables: ["silver/transactions", "silver/quarantine", "silver/labels"] },
  { name: "Gold", note: "Ready for the model", tables: ["gold/transaction_features"] },
  { name: "Checks", note: "About each batch", tables: ["quality/checks"] },
];

const ABOUT = {
  "bronze/transactions": "Every payment as it arrived, broken rows and duplicates included. One partition for each daily batch, and one for each day of the live feed.",
  "silver/quarantine": "The rows silver refused, each with the reason. Nothing is deleted: a rejected row stays here to be counted and explained.",
  "silver/transactions": "Clean payments, one row for each transaction_id. Each batch is merged in, so a rerun changes nothing.",
  "silver/labels": "Whether each payment was fraud, and the date that became known. Training only reads labels known by its cutoff.",
  "gold/transaction_features": "The features the model reads. An account's history uses its earlier payments only, never the same or later ones.",
  "quality/checks": "One row for each check on each batch. A failed critical check stops the daily run before gold.",
};

const STALE_HOURS = 36;
const PAGE_SIZES = [25, 50, 100];
const short = (table) => table.split("/")[1].replace("transaction_features", "features");
const when = (iso) =>
  new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const isTime = (type) => type.startsWith("timestamp");
// What a reader looks for first goes first: the ID, then why a row was refused.
const FIRST = ["transaction_id", "reason", "check", "passed", "is_fraud"];
const ordered = (columns) => [
  ...FIRST.map((name) => columns.find((column) => column.name === name)).filter(Boolean),
  ...columns.filter((column) => !FIRST.includes(column.name)),
];

function Cell({ column, value, onTrace }) {
  if (value == null) return <span className="text-ink-muted">null</span>;
  if (column.name === "transaction_id") {
    return (
      <button type="button" onClick={() => onTrace(value)} className="tabular text-series-1 underline-offset-2 hover:underline" title="Follow this payment through every layer">
        {value}
      </button>
    );
  }
  if (column.name === "reason") return <Pill tone="serious">{value}</Pill>;
  if (column.name === "passed") return value ? <Pill tone="good">Pass</Pill> : <Pill tone="critical">Fail</Pill>;
  if (column.name === "is_fraud") return value ? <Pill tone="critical">Fraud</Pill> : <Pill tone="good">Not fraud</Pill>;
  if (column.type === "bool") return <span className={value ? "text-ink" : "text-ink-muted"}>{value ? "✓ yes" : "no"}</span>;
  if (isTime(column.type)) return <span className="tabular text-ink-2">{when(value)}</span>;
  if (column.name === "amount") return <span className="tabular">{money(value)}</span>;
  if (column.type === "double") return <span className="tabular">{Number(value).toFixed(2)}</span>;
  return <span className={/int|long/.test(column.type) ? "tabular" : ""}>{String(value)}</span>;
}

function TableCard({ row, selected, onSelect }) {
  const stale = row.exists && (Date.now() - new Date(row.last_write).getTime()) / 3_600_000 > STALE_HOURS;
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`w-full rounded-lg border p-3 text-left transition-colors ${
        selected ? "border-series-1/70 bg-series-1/10" : "border-white/10 bg-white/[0.02] hover:border-white/25"
      }`}
    >
      <div className="flex items-center gap-2">
        <Icon name="database" className="h-4 w-4 shrink-0 text-ink-2" />
        <span className="truncate text-sm font-semibold text-ink">{short(row.table)}</span>
        {!row.exists && <span className="ml-auto text-xs text-critical">✕ Missing</span>}
      </div>
      {row.exists && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-ink-2">
          <span className="tabular">{row.rows.toLocaleString()} rows</span>
          <span className="tabular">{bytes(row.size_bytes)}</span>
          <span className={`tabular ${stale ? "text-warning" : ""}`}>
            {stale && <span aria-hidden="true">! </span>}written {age(row.last_write)}
          </span>
        </div>
      )}
    </button>
  );
}

function Step({ title, state, children }) {
  const look = {
    yes: { glyph: "✓", className: "border-good/50 text-good" },
    no: { glyph: "✕", className: "border-critical/50 text-critical" },
    none: { glyph: "·", className: "border-white/15 text-ink-muted" },
  }[state];
  return (
    <li className="relative pl-9">
      <span className={`absolute left-0 top-0.5 grid h-6 w-6 place-items-center rounded-full border bg-surface text-xs ${look.className}`} aria-hidden="true">
        {look.glyph}
      </span>
      <div className="text-sm font-medium text-ink">{title}</div>
      <div className="mt-0.5 text-xs leading-relaxed text-ink-2">{children}</div>
    </li>
  );
}

function Trace({ trace, onClose }) {
  if (!trace) {
    return (
      <Panel title="Follow a payment" note="Click any transaction ID in the table">
        <p className="px-4 py-14 text-center text-sm text-ink-muted">
          Pick a transaction ID to see where it went: bronze, then silver or quarantine, its label, its gold features, and any alert.
        </p>
      </Panel>
    );
  }
  if (trace === "loading") return <Panel title="Follow a payment"><p className="px-4 py-14 text-center text-sm text-ink-muted">Reading every layer.</p></Panel>;

  const layers = trace.layers;
  const bronze = layers["bronze/transactions"];
  const rejected = layers["silver/quarantine"];
  const silver = layers["silver/transactions"];
  const label = layers["silver/labels"][0];
  const gold = layers["gold/transaction_features"][0];
  const alert = trace.alert;
  const features = gold ? Object.entries(gold).filter(([key]) => !["transaction_id", "account_id", "ts"].includes(key)) : [];

  return (
    <Panel
      title={<span className="tabular">{trace.transaction_id}</span>}
      note="This payment in every layer"
      right={
        <button type="button" onClick={onClose} className="rounded-md px-2 py-0.5 text-lg leading-none text-ink-muted hover:bg-white/10 hover:text-ink" aria-label="Close the trace">
          ×
        </button>
      }
    >
      <ol className="relative space-y-4 p-4 before:absolute before:bottom-6 before:left-[27px] before:top-6 before:w-px before:bg-white/10">
        <Step title="Bronze" state={bronze.length ? "yes" : "none"}>
          {bronze.length === 0 && "Not in bronze. Retention keeps bronze for 7 days."}
          {bronze.length === 1 && `Arrived in batch ${bronze[0].batch_id}, ${when(bronze[0].ingested_at)}. ${money(bronze[0].amount)}, ${bronze[0].country}, ${bronze[0].channel}.`}
          {bronze.length > 1 && `Arrived ${bronze.length} times, in batch ${bronze[0].batch_id}. Silver keeps one copy and quarantines the rest.`}
        </Step>
        <Step title="Silver" state={silver.length ? "yes" : rejected.length ? "no" : "none"}>
          {silver.length > 0 && `Accepted. One clean row, account ${silver[0].account_id}.`}
          {rejected.length > 0 && (
            <span className="mt-1 flex flex-wrap items-center gap-1.5">
              {silver.length ? "A copy was quarantined:" : "Quarantined:"} {rejected.map((row, index) => <Pill key={index} tone="serious">{row.reason}</Pill>)}
            </span>
          )}
          {!silver.length && !rejected.length && "Not in silver yet. Live rows reach silver when the daily run takes them in."}
        </Step>
        <Step title="Label" state={label ? (label.is_fraud ? "no" : "yes") : "none"}>
          {label
            ? `${label.is_fraud ? "Fraud" : "Not fraud"}, known on ${when(label.labelled_at)}. Source: ${label.label_source}.`
            : "No verdict yet. A label arrives 30 to 90 days after the payment."}
        </Step>
        <Step title="Gold" state={gold ? "yes" : "none"}>
          {gold ? (
            <span className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5">
              {features.map(([key, value]) => (
                <span key={key} className="flex justify-between gap-2">
                  <span className="text-ink-muted">{reasonLabel(key)}</span>
                  <span className="tabular text-ink">{typeof value === "boolean" ? (value ? "yes" : "no") : typeof value === "number" ? Number(value.toFixed(2)).toLocaleString() : value}</span>
                </span>
              ))}
            </span>
          ) : (
            "No features. Only accepted payments reach gold."
          )}
        </Step>
        <Step title="Alert" state={alert ? "no" : "none"}>
          {alert ? (
            <span className="mt-1 block">
              <span className="flex flex-wrap items-center gap-2">
                Score <span className="tabular font-semibold text-critical">{alert.score.toFixed(3)}</span>
                <Pill tone={(ALERT_STATUS[alert.status] ?? ALERT_STATUS.open).tone}>{(ALERT_STATUS[alert.status] ?? ALERT_STATUS.open).label}</Pill>
              </span>
              <span className="mt-1 block">{alert.reason}</span>
            </span>
          ) : (
            "No alert. Only the live feed scores payments, and only a score over the threshold raises one."
          )}
        </Step>
      </ol>
    </Panel>
  );
}

export default function Data() {
  const [tables, setTables] = useState(null);
  const [table, setTable] = useState("bronze/transactions");
  const [batch, setBatch] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(25);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [trace, setTrace] = useState(null);

  useEffect(() => {
    const read = () => getLakeTables().then(setTables).catch(() => {});
    read();
    const timer = setInterval(read, 30_000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const load = useCallback(() => {
    setError("");
    getLakeRows({ table, batch, q: query, limit: size, offset: page * size })
      .then(setRows)
      .catch((reason) => setError(String(reason.message ?? reason)));
  }, [table, batch, query, page, size]);

  useEffect(() => {
    load();
  }, [load]);

  const choose = (name) => {
    setTable(name);
    setBatch("");
    setPage(0);
  };

  const onTrace = async (id) => {
    setTrace("loading");
    try {
      setTrace(await getTrace(id));
    } catch {
      setTrace(null);
    }
  };

  const columns = rows ? ordered(rows.columns) : [];
  const byName = Object.fromEntries((tables?.tables ?? []).map((row) => [row.table, row]));
  const batchList = tables?.batches?.[table] ?? [];
  const pageCount = rows ? Math.max(1, Math.ceil(rows.total / size)) : 1;
  const first = rows?.total ? page * size + 1 : 0;
  const last = rows ? Math.min(rows.total, (page + 1) * size) : 0;

  return (
    <div className="space-y-4">
      <Panel title="The lake, layer by layer" note="A payment moves left to right. Click a table to open it">
        <div className="grid gap-6 p-4 md:grid-cols-2 xl:grid-cols-4 xl:gap-8">
          {LAYERS.map((layer, index) => (
            <section key={layer.name} className="relative min-w-0 space-y-2">
              <div className="flex items-baseline gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-2">{layer.name}</h3>
                <span className="text-xs text-ink-muted">{layer.note}</span>
              </div>
              {layer.tables.map((name) =>
                byName[name] ? (
                  <TableCard key={name} row={byName[name]} selected={table === name} onSelect={() => choose(name)} />
                ) : (
                  <div key={name} className="h-16 animate-pulse rounded-lg border border-white/5 bg-white/[0.02]" />
                ),
              )}
              {index < 2 && (
                <span className="absolute -right-6 top-9 hidden text-ink-muted xl:block" aria-hidden="true">
                  →
                </span>
              )}
            </section>
          ))}
        </div>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-12 [&>*]:min-w-0">
        <section className="rounded-xl border border-white/10 bg-surface xl:col-span-8">
          <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-white/10 px-4 py-3">
            <div className="min-w-[12rem] flex-1">
              <h2 className="tabular text-[15px] font-semibold text-ink">
                {table} <span className="font-normal text-ink-muted">({(rows?.total ?? 0).toLocaleString()} rows)</span>
              </h2>
              <p className="text-xs text-ink-muted">{ABOUT[table]}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {batchList.length > 0 && (
                <>
                  <label htmlFor="data-batch" className="sr-only">
                    Batch
                  </label>
                  <select
                    id="data-batch"
                    value={batch}
                    onChange={(event) => {
                      setBatch(event.target.value);
                      setPage(0);
                    }}
                    className="tabular rounded-lg border border-white/15 bg-surface px-2.5 py-1.5 text-xs text-ink"
                  >
                    <option value="">All batches</option>
                    {batchList.map(([id, count]) => (
                      <option key={id} value={id}>
                        {id} ({count.toLocaleString()})
                      </option>
                    ))}
                  </select>
                </>
              )}
              {table !== "quality/checks" && (
                <>
                  <label htmlFor="data-search" className="sr-only">
                    Search by transaction or account
                  </label>
                  <input
                    id="data-search"
                    value={search}
                    onChange={(event) => {
                      setSearch(event.target.value);
                      setPage(0);
                    }}
                    placeholder="Transaction or account"
                    className="w-48 max-w-full rounded-lg border border-white/15 bg-plane px-3 py-1.5 text-xs text-ink placeholder:text-ink-muted"
                  />
                </>
              )}
            </div>
          </header>

          {error ? (
            <p className="px-4 py-12 text-center text-sm text-critical">Could not read the table: {error}</p>
          ) : !rows ? (
            <p className="px-4 py-12 text-center text-sm text-ink-muted">Reading.</p>
          ) : (
            <div className="relative max-h-[640px] overflow-auto">
              <table className="w-full whitespace-nowrap text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-left text-[11px] font-medium uppercase tracking-wide text-ink-muted">
                    {columns.map((column) => (
                      <th key={column.name} className="sticky top-0 bg-surface px-3 py-2 font-medium" title={column.description ? `${column.description} (${column.type})` : column.type}>
                        {column.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.rows.length === 0 && (
                    <tr>
                      <td colSpan={columns.length} className="px-4 py-12 text-center text-ink-muted">
                        No rows match.
                      </td>
                    </tr>
                  )}
                  {rows.rows.map((row, index) => (
                    <tr key={index} className="border-b border-white/5 last:border-0 hover:bg-white/[0.03]">
                      {columns.map((column) => (
                        <td key={column.name} className="px-3 py-1.5">
                          <Cell column={column} value={row[column.name]} onTrace={onTrace} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <footer className="flex flex-wrap items-center gap-3 border-t border-white/10 px-4 py-3 text-sm">
            <span className="tabular text-ink-muted">
              Showing {first.toLocaleString()} to {last.toLocaleString()} of {(rows?.total ?? 0).toLocaleString()}. Newest first
            </span>
            <span className="ml-auto flex items-center gap-1">
              <button type="button" onClick={() => setPage(page - 1)} disabled={page === 0} className="rounded-md px-2 py-1 text-ink-2 hover:bg-white/5 disabled:opacity-30" aria-label="Previous page">
                ‹
              </button>
              <span className="tabular px-2 text-ink-2">
                Page {(page + 1).toLocaleString()} of {pageCount.toLocaleString()}
              </span>
              <button type="button" onClick={() => setPage(page + 1)} disabled={page >= pageCount - 1} className="rounded-md px-2 py-1 text-ink-2 hover:bg-white/5 disabled:opacity-30" aria-label="Next page">
                ›
              </button>
            </span>
            <label htmlFor="data-size" className="sr-only">
              Rows on each page
            </label>
            <select
              id="data-size"
              value={size}
              onChange={(event) => {
                setSize(Number(event.target.value));
                setPage(0);
              }}
              className="rounded-lg border border-white/15 bg-surface px-2.5 py-1.5 text-xs text-ink"
            >
              {PAGE_SIZES.map((value) => (
                <option key={value} value={value}>
                  {value} / page
                </option>
              ))}
            </select>
          </footer>
        </section>

        <div className="xl:col-span-4">
          <div className="xl:sticky xl:top-4">
            <Trace trace={trace} onClose={() => setTrace(null)} />
          </div>
        </div>
      </div>
    </div>
  );
}
