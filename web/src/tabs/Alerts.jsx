import { useCallback, useEffect, useState } from "react";
import { Button, Panel, Pill, Stat } from "../components/Chrome.jsx";
import { decide, exportLabels, getAlerts, money } from "../api.js";

const FILTERS = [
  { key: "", label: "All" },
  { key: "open", label: "Open" },
  { key: "confirmed_fraud", label: "Confirmed" },
  { key: "false_positive", label: "False positive" },
];

const STATUS = {
  open: { tone: "warning", label: "open" },
  confirmed_fraud: { tone: "critical", label: "confirmed fraud" },
  false_positive: { tone: "good", label: "false positive" },
};

function Card({ alert, onDecide }) {
  const status = STATUS[alert.status] ?? STATUS.open;
  return (
    <article className="rounded-lg border border-white/10 bg-surface p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="tabular text-base font-semibold text-ink">{alert.account_id}</span>
        <span className="tabular text-base text-ink">{money(alert.amount)}</span>
        <span className="text-sm text-ink-muted">
          {alert.category} · {alert.channel} · {alert.country}
        </span>
        <span className="ml-auto flex items-center gap-2">
          <Pill tone={status.tone}>{status.label}</Pill>
          <span className="tabular text-sm font-semibold text-critical">{alert.score.toFixed(3)}</span>
        </span>
      </div>

      <p className="mt-2 text-sm text-ink-2">{alert.reason}</p>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {Object.entries(alert.contributions ?? {}).map(([feature, value]) => (
          <span
            key={feature}
            className="tabular rounded border border-white/10 px-1.5 py-0.5 text-[11px] text-ink-muted"
          >
            {feature} {value > 0 ? "+" : ""}
            {value.toFixed(2)}
          </span>
        ))}
      </div>

      <div className="mt-3 flex items-center gap-2 border-t border-white/5 pt-3">
        <span className="text-[11px] text-ink-muted">
          model v{alert.model_version} · threshold {alert.threshold.toFixed(3)} · #{alert.alert_id}
        </span>
        {alert.status === "open" && (
          <span className="ml-auto flex gap-2">
            <Button tone="critical" onClick={() => onDecide(alert.alert_id, "confirmed_fraud")}>
              Confirm fraud
            </Button>
            <Button tone="good" onClick={() => onDecide(alert.alert_id, "false_positive")}>
              False positive
            </Button>
          </span>
        )}
      </div>
    </article>
  );
}

export default function Alerts() {
  const [data, setData] = useState({ alerts: [], counts: {} });
  const [filter, setFilter] = useState("");
  const [exported, setExported] = useState(null);

  const load = useCallback(async () => setData(await getAlerts(filter)), [filter]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 5_000);
    return () => clearInterval(timer);
  }, [load]);

  const onDecide = async (id, status) => {
    await decide(id, status);
    load();
  };

  const onExport = async () => {
    const answer = await exportLabels();
    setExported(answer.written);
    load();
  };

  const counts = data.counts ?? {};
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Open" value={counts.open ?? 0} tone={counts.open ? "critical" : "ink"} />
        <Stat label="Confirmed fraud" value={counts.confirmed_fraud ?? 0} />
        <Stat label="False positive" value={counts.false_positive ?? 0} />
      </div>

      <Panel
        title="Alerts"
        note="A decision here becomes a label the next retrain reads"
        right={
          <div className="flex flex-wrap items-center gap-2">
            {FILTERS.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setFilter(option.key)}
                className={`rounded-md border px-2 py-1 text-xs ${
                  filter === option.key
                    ? "border-white/40 text-ink"
                    : "border-white/10 text-ink-muted hover:border-white/25"
                }`}
              >
                {option.label}
              </button>
            ))}
            <Button onClick={onExport}>
              {exported === null ? "Export labels" : `Exported ${exported}`}
            </Button>
          </div>
        }
      >
        <div className="space-y-3 p-3">
          {data.alerts.length === 0 && (
            <p className="py-8 text-center text-sm text-ink-muted">No alerts with that status.</p>
          )}
          {data.alerts.map((alert) => (
            <Card key={alert.alert_id} alert={alert} onDecide={onDecide} />
          ))}
        </div>
      </Panel>
    </div>
  );
}
