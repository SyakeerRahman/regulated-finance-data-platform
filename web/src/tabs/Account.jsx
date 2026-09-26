import { useState } from "react";
import { Button, Panel, Pill, Stat, Table } from "../components/Chrome.jsx";
import { clock, getAccount, money } from "../api.js";

export default function Account() {
  const [query, setQuery] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const look = async (event) => {
    event.preventDefault();
    setError("");
    try {
      setData(await getAccount(query.trim()));
    } catch {
      setData(null);
      setError(`No transactions for ${query.trim()}.`);
    }
  };

  return (
    <div className="space-y-4">
      <Panel title="Look up an account" note="Its history, its features, and anything raised against it">
        <form onSubmit={look} className="flex flex-wrap gap-2 p-4">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="ACC01518"
            className="tabular w-48 rounded-md border border-white/15 bg-plane px-3 py-1.5 text-sm text-ink placeholder:text-ink-muted"
          />
          <Button onClick={look}>Look up</Button>
        </form>
        {error && <p className="px-4 pb-4 text-sm text-critical">{error}</p>}
      </Panel>

      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Transactions" value={data.transactions.toLocaleString()} />
            <Stat label="Mean amount" value={money(data.mean_amount)} />
            <Stat label="Largest vs account" value={`${data.max_vs_account.toFixed(1)}x`} />
            <Stat
              label="Alerts"
              value={data.alerts.length}
              tone={data.alerts.length ? "critical" : "ink"}
              sub={`${(data.abroad_share * 100).toFixed(1)}% abroad`}
            />
          </div>

          {data.alerts.length > 0 && (
            <Panel title="Raised against this account">
              <ul className="space-y-2 p-4 text-sm">
                {data.alerts.map((alert) => (
                  <li key={alert.alert_id} className="flex flex-wrap items-baseline gap-2">
                    <Pill tone={alert.status === "open" ? "warning" : "muted"}>{alert.status}</Pill>
                    <span className="tabular text-ink">{money(alert.amount)}</span>
                    <span className="tabular text-critical">{alert.score.toFixed(3)}</span>
                    <span className="text-ink-2">{alert.reason}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          <Panel title="Recent transactions" note="Newest first, with the features the model reads">
            <Table
              rows={data.recent}
              rowKey={(row) => row.transaction_id}
              columns={[
                { key: "ts", label: "Time", mono: true, render: (row) => `${row.ts.slice(0, 10)} ${clock(row.ts)}` },
                { key: "amount", label: "Amount", right: true, render: (row) => money(row.amount) },
                {
                  key: "amount_vs_account",
                  label: "vs account",
                  right: true,
                  render: (row) => `${row.amount_vs_account.toFixed(2)}x`,
                },
                { key: "hour", label: "Hour", right: true, hideSmall: true },
                {
                  key: "flags",
                  label: "Flags",
                  render: (row) =>
                    [
                      row.is_night && "night",
                      row.is_abroad && "abroad",
                      row.is_online && "online",
                      row.is_risky_category && "risky category",
                    ]
                      .filter(Boolean)
                      .join(", ") || "-",
                },
                { key: "prior_transactions", label: "Prior", right: true, hideSmall: true },
              ]}
            />
          </Panel>

          <Panel title="Coming in the AI layer">
            <p className="px-4 py-3 text-sm text-ink-2">
              This tab answers a lookup. Stage G turns it into a question in plain words, answered
              from these same rows, with the fraud policy rule each alert breaks cited beside it.
            </p>
          </Panel>
        </>
      )}
    </div>
  );
}
