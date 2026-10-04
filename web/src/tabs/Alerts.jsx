import { useCallback, useEffect, useMemo, useState } from "react";
import { Panel, Pill } from "../components/Chrome.jsx";
import { Icon } from "../components/Icons.jsx";
import Kpi, { change } from "../components/Kpi.jsx";
import TopList from "../components/TopList.jsx";
import AlertDetails from "../components/AlertDetails.jsx";
import { AlertsOverTime, StatusLegend, StatusRing, hourlySlots } from "../components/AlertCharts.jsx";
import {
  ALERT_STATUS,
  COUNTRY_NAMES,
  REASON_LABELS,
  clock,
  decide,
  decideMany,
  exportLabels,
  getAlert,
  getAlertSummary,
  money,
  pretty,
  reasonLabel,
  searchAlerts,
} from "../api.js";

const RANGES = [
  { value: "1", label: "Last hour" },
  { value: "24", label: "Last 24 hours" },
  { value: "168", label: "Last 7 days" },
  { value: "", label: "All time" },
];
const PAGE_SIZES = [10, 25, 50];
const REFRESH_MS = 10_000;

function Select({ id, label, value, onChange, children }) {
  return (
    <>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-lg border border-white/15 bg-surface px-2.5 py-1.5 text-xs text-ink"
      >
        {children}
      </select>
    </>
  );
}

/** Page numbers around the current one, with the first and last always reachable. */
function pageList(current, count) {
  const pages = new Set([0, count - 1, current - 1, current, current + 1]);
  const sorted = [...pages].filter((page) => page >= 0 && page < count).sort((a, b) => a - b);
  const out = [];
  sorted.forEach((page, index) => {
    if (index && page - sorted[index - 1] > 1) out.push("gap-" + page);
    out.push(page);
  });
  return out;
}

export default function Alerts({ onOpenAccount }) {
  const [summary, setSummary] = useState(null);
  const [filters, setFilters] = useState({ hours: "24", status: "", reason: "", country: "" });
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(10);
  const [data, setData] = useState({ alerts: [], total: 0 });
  const [selected, setSelected] = useState(new Set());
  // undefined until the first load picks an alert, null once the reader closes the panel.
  const [detail, setDetail] = useState(undefined);
  const [exported, setExported] = useState(null);

  // Wait until typing stops, so each key press is not a query.
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const loadSummary = useCallback(() => {
    getAlertSummary().then(setSummary).catch(() => {});
  }, []);

  const loadList = useCallback(() => {
    searchAlerts({ ...filters, q: query, limit: size, offset: page * size })
      .then((answer) => {
        setData(answer);
        // Keep the open alert current, so a decision made elsewhere shows here too.
        setDetail((open) => {
          if (open === undefined) return answer.alerts[0];
          return open && (answer.alerts.find((row) => row.alert_id === open.alert_id) ?? open);
        });
      })
      .catch(() => {});
  }, [filters, query, page, size]);

  useEffect(() => {
    loadSummary();
    const timer = setInterval(loadSummary, REFRESH_MS);
    return () => clearInterval(timer);
  }, [loadSummary]);

  useEffect(() => {
    loadList();
    const timer = setInterval(loadList, REFRESH_MS);
    return () => clearInterval(timer);
  }, [loadList]);

  const setFilter = (key) => (value) => {
    setFilters((previous) => ({ ...previous, [key]: value }));
    setPage(0);
    setSelected(new Set());
  };

  const refreshAll = () => {
    loadList();
    loadSummary();
  };

  const onDecide = async (id, status) => {
    const row = await decide(id, status);
    setDetail((open) => (open && open.alert_id === id ? { ...open, ...row } : open));
    refreshAll();
  };

  // A cited past case may be on another page of the list, so it is fetched by id.
  const onOpenAlert = (id) => {
    getAlert(id).then(setDetail).catch(() => {});
  };

  const onBulk = async (status) => {
    await decideMany([...selected], status);
    setSelected(new Set());
    refreshAll();
  };

  const onExport = async () => {
    const answer = await exportLabels();
    setExported(answer.written);
  };

  const toggle = (id) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allOnPage = data.alerts.length > 0 && data.alerts.every((row) => selected.has(row.alert_id));
  const toggleAll = () =>
    setSelected((previous) => {
      const next = new Set(previous);
      for (const row of data.alerts) {
        if (allOnPage) next.delete(row.alert_id);
        else next.add(row.alert_id);
      }
      return next;
    });

  const tiles = useMemo(() => {
    if (!summary) return null;
    const now = summary.current;
    const before = summary.previous;
    const slots = hourlySlots(summary.hourly);
    const sum = (counts) => Object.values(counts ?? {}).reduce((total, value) => total + value, 0);
    const series = (status) => slots.map((slot) => (status ? slot[status] : slot.open + slot.confirmed_fraud + slot.false_positive));
    const tile = (status) => ({
      value: status ? (now[status] ?? 0) : sum(now),
      delta: change(status ? (now[status] ?? 0) : sum(now), before ? (status ? (before[status] ?? 0) : sum(before)) : null, { higherIsBad: true }),
      spark: series(status),
    });
    return { slots, total: tile(null), confirmed: tile("confirmed_fraud"), open: tile("open"), falsePositive: tile("false_positive") };
  }, [summary]);

  const sub = summary?.previous ? "vs. previous 24 hours" : "last 24 hours";
  const pageCount = Math.max(1, Math.ceil(data.total / size));
  const first = data.total ? page * size + 1 : 0;
  const last = Math.min(data.total, (page + 1) * size);
  const reasonTotal = (summary?.reasons ?? []).reduce((total, [, count]) => total + count, 0);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon="alert" iconTone="red" label="Total alerts (24h)" value={(tiles?.total.value ?? 0).toLocaleString()} delta={tiles?.total.delta} sub={sub} spark={tiles?.total.spark} sparkColor="var(--critical)" />
        <Kpi icon="bell" iconTone="red" label="Confirmed fraud" value={(tiles?.confirmed.value ?? 0).toLocaleString()} delta={tiles?.confirmed.delta} sub={sub} spark={tiles?.confirmed.spark} sparkColor="var(--critical)" />
        <Kpi icon="inbox" iconTone="amber" label="Open alerts" value={(tiles?.open.value ?? 0).toLocaleString()} delta={tiles?.open.delta} sub={sub} spark={tiles?.open.spark} sparkColor="var(--warning)" />
        <Kpi icon="why" iconTone="blue" label="False positives" value={(tiles?.falsePositive.value ?? 0).toLocaleString()} delta={tiles?.falsePositive.delta} sub={sub} spark={tiles?.falsePositive.spark} sparkColor="var(--series-1)" />
      </div>

      <div className="grid gap-4 xl:grid-cols-12 [&>*]:min-w-0">
        <Panel className="xl:col-span-6" title="Alerts over time" note="Each hour of the last 24, by what has happened to them since" right={<StatusLegend />}>
          <AlertsOverTime slots={tiles?.slots ?? []} />
        </Panel>
        <Panel className="xl:col-span-3" title="Alerts by risk reason" note="The feature that raised each score most">
          <TopList rows={(summary?.reasons ?? []).slice(0, 6)} total={reasonTotal} label={reasonLabel} empty="No alerts in the last 24 hours." />
        </Panel>
        <Panel className="xl:col-span-3" title="Alerts by status" note="Raised in the last 24 hours">
          <StatusRing counts={summary?.current ?? {}} />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-12 [&>*]:min-w-0">
        <section className="rounded-xl border border-white/10 bg-surface xl:col-span-8">
          <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-white/10 px-4 py-3">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold text-ink">
              <Icon name="inbox" className="h-5 w-5 text-ink-2" />
              Alert list <span className="tabular font-normal text-ink-muted">({data.total.toLocaleString()})</span>
            </h2>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <Select id="alert-range" label="Time range" value={filters.hours} onChange={setFilter("hours")}>
                {RANGES.map((range) => (
                  <option key={range.label} value={range.value}>
                    {range.label}
                  </option>
                ))}
              </Select>
              <Select id="alert-status" label="Status" value={filters.status} onChange={setFilter("status")}>
                <option value="">Status: all</option>
                {Object.entries(ALERT_STATUS).map(([key, status]) => (
                  <option key={key} value={key}>
                    {status.label}
                  </option>
                ))}
              </Select>
              <Select id="alert-reason" label="Risk reason" value={filters.reason} onChange={setFilter("reason")}>
                <option value="">Reason: all</option>
                {Object.entries(REASON_LABELS).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </Select>
              <Select id="alert-country" label="Country" value={filters.country} onChange={setFilter("country")}>
                <option value="">Country: all</option>
                {Object.entries(COUNTRY_NAMES).map(([code, name]) => (
                  <option key={code} value={code}>
                    {name}
                  </option>
                ))}
              </Select>
              <label htmlFor="alert-search" className="sr-only">
                Search by transaction, account or category
              </label>
              <input
                id="alert-search"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(0);
                }}
                placeholder="Search transaction, account, category"
                className="w-60 max-w-full rounded-lg border border-white/15 bg-plane px-3 py-1.5 text-xs text-ink placeholder:text-ink-muted"
              />
            </div>
          </header>

          <div className="flex min-h-11 flex-wrap items-center gap-2 border-b border-white/5 px-4 py-2 text-xs">
            {selected.size > 0 ? (
              <>
                <span className="text-ink">{selected.size} selected</span>
                <button type="button" onClick={() => onBulk("confirmed_fraud")} className="rounded-md border border-critical/60 px-2.5 py-1 font-medium text-critical hover:bg-critical/10">
                  Confirm fraud
                </button>
                <button type="button" onClick={() => onBulk("false_positive")} className="rounded-md border border-white/20 px-2.5 py-1 font-medium text-ink hover:bg-white/5">
                  False positive
                </button>
                <button type="button" onClick={() => setSelected(new Set())} className="px-1.5 py-1 text-ink-muted hover:text-ink">
                  Clear
                </button>
              </>
            ) : (
              <span className="text-ink-muted">Select alerts to decide several at once. Refreshes every 10 seconds.</span>
            )}
            <button type="button" onClick={onExport} className="ml-auto rounded-md border border-white/15 px-2.5 py-1 text-ink hover:border-white/35" title="Write every decision into silver/labels for the next retrain">
              {exported === null ? "Export decisions to labels" : `Exported ${exported} labels`}
            </button>
          </div>

          <div className="relative overflow-x-auto">
            <table className="w-full whitespace-nowrap text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-[11px] font-medium uppercase tracking-wide text-ink-muted">
                  <th className="w-10 px-3 py-2">
                    <input type="checkbox" checked={allOnPage} onChange={toggleAll} aria-label="Select every alert on this page" className="accent-[var(--series-1)]" />
                  </th>
                  <th className="px-2 py-2 font-medium">Time</th>
                  <th className="hidden px-2 py-2 font-medium md:table-cell">Transaction ID</th>
                  <th className="px-2 py-2 font-medium">Account</th>
                  <th className="px-2 py-2 text-right font-medium">Amount</th>
                  <th className="hidden px-2 py-2 font-medium lg:table-cell">Category</th>
                  <th className="hidden px-2 py-2 font-medium md:table-cell">Country</th>
                  <th className="hidden px-2 py-2 font-medium min-[1700px]:table-cell">Channel</th>
                  <th className="px-2 py-2 text-right font-medium">Score</th>
                  <th className="hidden px-2 py-2 font-medium md:table-cell">Risk reason</th>
                  <th className="px-2 py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.alerts.length === 0 && (
                  <tr>
                    <td colSpan={11} className="px-4 py-12 text-center text-ink-muted">
                      No alerts match these filters.
                    </td>
                  </tr>
                )}
                {data.alerts.map((row) => {
                  const active = detail?.alert_id === row.alert_id;
                  const status = ALERT_STATUS[row.status] ?? ALERT_STATUS.open;
                  return (
                    <tr
                      key={row.alert_id}
                      onClick={() => setDetail(row)}
                      className={`cursor-pointer border-b border-white/5 last:border-0 ${
                        active ? "bg-critical/15 shadow-[inset_3px_0_0_var(--critical)]" : "hover:bg-white/[0.03]"
                      }`}
                    >
                      <td className="px-3 py-2" onClick={(event) => event.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selected.has(row.alert_id)}
                          onChange={() => toggle(row.alert_id)}
                          aria-label={`Select alert ${row.alert_id}`}
                          className="accent-[var(--series-1)]"
                        />
                      </td>
                      <td className="tabular px-2 py-2 text-ink-2">{clock(row.created_at)}</td>
                      <td className="tabular hidden px-2 py-2 md:table-cell">{row.transaction_id}</td>
                      <td className="tabular px-2 py-2">{row.account_id}</td>
                      <td className="tabular px-2 py-2 text-right">{money(row.amount)}</td>
                      <td className="hidden px-2 py-2 lg:table-cell">{pretty(row.category)}</td>
                      <td className="hidden px-2 py-2 md:table-cell">{row.country}</td>
                      <td className="hidden px-2 py-2 min-[1700px]:table-cell">{row.channel}</td>
                      <td className="tabular px-2 py-2 text-right font-semibold text-critical">{row.score.toFixed(3)}</td>
                      <td className="hidden px-2 py-2 text-ink-2 md:table-cell">{reasonLabel(row.top_reason)}</td>
                      <td className="px-2 py-2">
                        <Pill tone={status.tone}>{status.label}</Pill>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <footer className="flex flex-wrap items-center gap-3 border-t border-white/10 px-4 py-3 text-sm">
            <span className="tabular text-ink-muted">
              Showing {first.toLocaleString()} to {last.toLocaleString()} of {data.total.toLocaleString()} alerts
            </span>
            <nav className="ml-auto flex items-center gap-1" aria-label="Pages">
              <button type="button" onClick={() => setPage(page - 1)} disabled={page === 0} className="rounded-md px-2 py-1 text-ink-2 hover:bg-white/5 disabled:opacity-30" aria-label="Previous page">
                ‹
              </button>
              {pageList(page, pageCount).map((item) =>
                typeof item === "string" ? (
                  <span key={item} className="px-1 text-ink-muted">
                    …
                  </span>
                ) : (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setPage(item)}
                    aria-current={item === page ? "page" : undefined}
                    className={`tabular min-w-8 rounded-md px-2 py-1 ${item === page ? "bg-white/10 text-ink" : "text-ink-2 hover:bg-white/5"}`}
                  >
                    {item + 1}
                  </button>
                ),
              )}
              <button type="button" onClick={() => setPage(page + 1)} disabled={page >= pageCount - 1} className="rounded-md px-2 py-1 text-ink-2 hover:bg-white/5 disabled:opacity-30" aria-label="Next page">
                ›
              </button>
            </nav>
            <Select
              id="alert-page-size"
              label="Alerts on each page"
              value={size}
              onChange={(value) => {
                setSize(Number(value));
                setPage(0);
              }}
            >
              {PAGE_SIZES.map((value) => (
                <option key={value} value={value}>
                  {value} / page
                </option>
              ))}
            </Select>
          </footer>
        </section>

        <div className="xl:col-span-4">
          <AlertDetails alert={detail} onDecide={onDecide} onClose={() => setDetail(null)} onOpenAccount={onOpenAccount} onOpenAlert={onOpenAlert} />
        </div>
      </div>
    </div>
  );
}
