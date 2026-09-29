import { useCallback, useEffect, useState } from "react";
import { Pill } from "./Chrome.jsx";
import { Icon } from "./Icons.jsx";
import { COUNTRY_NAMES, clock, decide, getLatestAlert, money, pretty } from "../api.js";

const STATUS = {
  confirmed_fraud: { tone: "critical", label: "Confirmed fraud" },
  false_positive: { tone: "good", label: "False positive" },
};

function ago(iso) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 2) return "just now";
  if (seconds < 60) return `${seconds} seconds ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} minutes ago`;
  return `${Math.round(seconds / 3600)} hours ago`;
}

/** The newest alert from Postgres, not from the stream: only the stored row has an id, and a
 *  decision needs one. */
export default function LatestAlert() {
  const [alert, setAlert] = useState(null);

  const load = useCallback(() => {
    getLatestAlert()
      .then((answer) => setAlert(answer.alerts[0] ?? null))
      .catch(() => setAlert(null));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 3_000);
    return () => clearInterval(timer);
  }, [load]);

  const onDecide = async (status) => {
    await decide(alert.alert_id, status);
    load();
  };

  return (
    <section className="rounded-xl border border-white/10 bg-surface">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-white/10 px-4 py-3">
        <Icon name="alert" className="h-5 w-5 text-critical" />
        <h2 className="text-[15px] font-semibold text-ink">Latest alert</h2>
        {alert && (
          <span className="tabular ml-auto text-xs text-ink-muted">
            {clock(alert.created_at)} ({ago(alert.created_at)})
          </span>
        )}
      </header>

      {!alert ? (
        <p className="px-4 py-10 text-center text-sm text-ink-muted">No alerts yet.</p>
      ) : (
        <div className="grid gap-4 p-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            {[
              ["Transaction", alert.transaction_id],
              ["Account", alert.account_id],
              ["Amount", money(alert.amount)],
              ["Country", `${alert.country} (${COUNTRY_NAMES[alert.country] ?? alert.country})`],
              ["Channel", alert.channel],
              ["Category", pretty(alert.category)],
            ].map(([term, value]) => (
              <div key={term} className="contents">
                <dt className="text-ink-muted">{term}</dt>
                <dd className="tabular truncate text-ink">{value}</dd>
              </div>
            ))}
            <dt className="text-ink-muted">Score</dt>
            <dd className="tabular font-semibold text-critical">
              {alert.score.toFixed(3)} <span className="font-normal text-ink-muted">model v{alert.model_version}</span>
            </dd>
          </dl>

          <div className="flex flex-col gap-3">
            <div className="flex-1 rounded-lg border border-white/10 bg-white/[0.03] p-3">
              <div className="flex items-center gap-2 text-sm font-medium text-warning">
                <Icon name="why" className="h-4 w-4" />
                Why flagged?
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-2">{alert.reason}</p>
            </div>

            {alert.status === "open" ? (
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => onDecide("confirmed_fraud")}
                  className="flex-1 rounded-lg border border-critical/60 px-3 py-2 text-sm font-medium text-critical transition-colors hover:bg-critical/10"
                >
                  Confirm fraud
                </button>
                <button
                  type="button"
                  onClick={() => onDecide("false_positive")}
                  className="flex-1 rounded-lg border border-white/20 px-3 py-2 text-sm font-medium text-ink transition-colors hover:bg-white/5"
                >
                  False positive
                </button>
              </div>
            ) : (
              <div>
                <Pill tone={STATUS[alert.status]?.tone ?? "muted"}>{STATUS[alert.status]?.label ?? alert.status}</Pill>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
