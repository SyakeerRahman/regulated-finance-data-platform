import { useState } from "react";
import { Pill } from "./Chrome.jsx";
import { Icon } from "./Icons.jsx";
import { AiNarrative, PolicyCitation, SimilarAlerts } from "./AiPanel.jsx";
import { ALERT_STATUS, COUNTRY_NAMES, money, pretty, reasonLabel } from "../api.js";

function CopyButton({ value, label }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // A browser that refuses the clipboard still shows the value, which can be selected by hand.
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      className="ml-1.5 rounded px-1 text-[11px] text-ink-muted hover:bg-white/10 hover:text-ink"
      aria-label={`Copy ${label}`}
    >
      {copied ? "copied" : "copy"}
    </button>
  );
}

const when = (iso) =>
  new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** One alert in full: the payment, the score, what moved it, and the decision. */
export default function AlertDetails({ alert, onDecide, onClose, onOpenAccount }) {
  if (!alert) {
    return (
      <section className="rounded-xl border border-white/10 bg-surface">
        <header className="border-b border-white/10 px-4 py-3">
          <h2 className="text-[15px] font-semibold text-ink">Alert details</h2>
        </header>
        <p className="px-4 py-16 text-center text-sm text-ink-muted">Select an alert in the list.</p>
      </section>
    );
  }

  const status = ALERT_STATUS[alert.status] ?? ALERT_STATUS.open;
  const contributions = Object.entries(alert.contributions ?? {}).sort((a, b) => b[1] - a[1]);
  const largest = Math.max(...contributions.map(([, value]) => Math.abs(value)), 0.01);

  return (
    <section className="rounded-xl border border-white/10 bg-surface">
      <header className="flex items-center gap-3 border-b border-white/10 px-4 py-3">
        <h2 className="text-[15px] font-semibold text-ink">Alert details</h2>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto rounded-md px-2 py-0.5 text-lg leading-none text-ink-muted hover:bg-white/10 hover:text-ink"
          aria-label="Close the details"
        >
          ×
        </button>
      </header>

      <div className="space-y-4 p-4">
        <div className="flex items-start gap-3">
          <Icon name="alert" className="mt-0.5 h-6 w-6 shrink-0 text-critical" />
          <div className="min-w-0 flex-1">
            <div className="font-semibold text-critical">Alert #{alert.alert_id}</div>
            <div className="text-xs text-ink-muted">
              Flagged by model v{alert.model_version} at threshold {alert.threshold.toFixed(3)}
            </div>
          </div>
          <Pill tone={status.tone}>{status.label}</Pill>
        </div>

        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
          <dt className="text-ink-muted">Transaction</dt>
          <dd className="tabular flex min-w-0 items-center text-ink">
            <span className="truncate">{alert.transaction_id}</span>
            <CopyButton value={alert.transaction_id} label="transaction ID" />
          </dd>
          <dt className="text-ink-muted">Account</dt>
          <dd className="tabular flex min-w-0 items-center text-ink">
            <span className="truncate">{alert.account_id}</span>
            <CopyButton value={alert.account_id} label="account" />
          </dd>
          {[
            ["Amount", money(alert.amount)],
            ["Category", pretty(alert.category)],
            ["Country", `${alert.country} (${COUNTRY_NAMES[alert.country] ?? alert.country})`],
            ["Channel", alert.channel],
            ["Time", when(alert.occurred_at)],
            ["Risk reason", reasonLabel(alert.top_reason)],
          ].map(([term, value]) => (
            <div key={term} className="contents">
              <dt className="text-ink-muted">{term}</dt>
              <dd className="tabular truncate text-ink">{value}</dd>
            </div>
          ))}
          <dt className="text-ink-muted">Score</dt>
          <dd className="tabular font-semibold text-critical">{alert.score.toFixed(3)}</dd>
        </dl>

        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
          <div className="flex items-center gap-2 text-sm font-medium text-warning">
            <Icon name="why" className="h-4 w-4" />
            Why this was flagged
          </div>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-2">{alert.reason}</p>
          {contributions.length > 0 && (
            <ul className="mt-3 space-y-1.5" aria-label="How much each feature moved the score">
              {contributions.map(([feature, value]) => (
                <li key={feature} className="grid grid-cols-[8.5rem_1fr_3rem] items-center gap-2 text-xs">
                  <span className="truncate text-ink-2">{reasonLabel(feature)}</span>
                  <span className="h-1.5 overflow-hidden rounded-full bg-white/5" aria-hidden="true">
                    <span
                      className={`block h-full rounded-full ${value > 0 ? "bg-series-2" : "bg-ink-muted"}`}
                      style={{ width: `${(Math.abs(value) / largest) * 100}%` }}
                    />
                  </span>
                  <span className="tabular text-right text-ink-muted">
                    {value > 0 ? "+" : ""}
                    {value.toFixed(2)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <PolicyCitation ruleIds={alert.policy_rules} />
        <AiNarrative alert={alert} />
        <SimilarAlerts alertId={alert.alert_id} />

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => onDecide(alert.alert_id, "confirmed_fraud")}
            disabled={alert.status === "confirmed_fraud"}
            className="flex-1 rounded-lg border border-critical/60 px-3 py-2 text-sm font-medium text-critical transition-colors hover:bg-critical/10 disabled:opacity-40"
          >
            Confirm fraud
          </button>
          <button
            type="button"
            onClick={() => onDecide(alert.alert_id, "false_positive")}
            disabled={alert.status === "false_positive"}
            className="flex-1 rounded-lg border border-white/20 px-3 py-2 text-sm font-medium text-ink transition-colors hover:bg-white/5 disabled:opacity-40"
          >
            False positive
          </button>
          <button
            type="button"
            onClick={() => onOpenAccount(alert.account_id)}
            className="rounded-lg border border-white/20 px-3 py-2 text-sm text-ink-2 transition-colors hover:bg-white/5 hover:text-ink"
          >
            Account history
          </button>
        </div>
        {alert.status !== "open" && (
          <p className="text-xs text-ink-muted">Decided. Pressing the other button changes the decision, and the next export replaces the label.</p>
        )}
      </div>
    </section>
  );
}
