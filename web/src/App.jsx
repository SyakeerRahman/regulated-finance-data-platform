import { useCallback, useEffect, useState } from "react";
import Live from "./tabs/Live.jsx";
import Alerts from "./tabs/Alerts.jsx";
import Model from "./tabs/Model.jsx";
import Pipeline from "./tabs/Pipeline.jsx";
import Account from "./tabs/Account.jsx";
import Data from "./tabs/Data.jsx";
import Ask from "./tabs/Ask.jsx";
import { Icon } from "./components/Icons.jsx";
import { getState, start, stop } from "./api.js";

const TABS = [
  { key: "live", label: "Live", icon: "live", title: "Real-Time Fraud Detection", note: "Scoring every payment as it arrives" },
  { key: "alerts", label: "Alerts", icon: "bell", title: "Alerts", note: "Investigate suspicious payments. Each decision becomes a label for the next retrain" },
  { key: "model", label: "Model", icon: "model", title: "Model", note: "How good the live model is, what it leans on, and whether the stream still looks like its training data" },
  { key: "pipeline", label: "Pipeline", icon: "pipeline", title: "Pipeline", note: "What runs, what it wrote, and whether each part answers" },
  { key: "data", label: "Data", icon: "database", title: "Data", note: "The lake, layer by layer: bronze, silver and gold, down to single rows" },
  { key: "ask", label: "Ask AI", icon: "chat", title: "Ask AI", note: "Questions about the live data, answered with read-only tools" },
  { key: "account", label: "Account", icon: "account", title: "Account", note: "One account's history, features and alerts" },
];

// Airflow and MLflow listen on 127.0.0.1 only. A visitor through the public tunnel cannot reach
// them, so the links show only on localhost: this PC, or the server over an SSH tunnel.
const TOOLS = [
  { label: "Airflow", icon: "airflow", href: "http://localhost:8095" },
  { label: "MLflow", icon: "bars", href: "http://localhost:8096" },
];
const IS_LOCAL = ["localhost", "127.0.0.1"].includes(window.location.hostname);

const RATES = [1, 3, 10, 50];

function Sidebar({ tab, setTab, openAlerts }) {
  return (
    <aside className="border-b border-white/10 bg-plane lg:sticky lg:top-0 lg:h-screen lg:w-56 lg:shrink-0 lg:border-b-0 lg:border-r">
      <div className="flex items-center gap-2.5 px-4 py-4 lg:px-5 lg:py-6">
        <Icon name="shield" className="h-7 w-7 text-series-1" />
        <span className="text-xl font-semibold tracking-tight text-ink">finplat</span>
      </div>

      {/* A row that scrolls on a phone, a column from lg up. */}
      <nav className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-col lg:px-3 lg:pb-0">
        {TABS.map((option) => {
          const active = tab === option.key;
          return (
            <button
              key={option.key}
              type="button"
              onClick={() => setTab(option.key)}
              aria-current={active ? "page" : undefined}
              className={`flex shrink-0 items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${
                active
                  ? "bg-series-1/15 font-medium text-series-1 lg:shadow-[inset_3px_0_0_var(--series-1)]"
                  : "text-ink-2 hover:bg-white/5 hover:text-ink"
              }`}
            >
              <Icon name={option.icon} className="h-5 w-5" />
              {option.label}
              {option.key === "alerts" && openAlerts > 0 && (
                <span className="tabular ml-auto rounded-full bg-critical px-1.5 text-[11px] font-semibold text-white">
                  {openAlerts}
                  <span className="sr-only"> open</span>
                </span>
              )}
            </button>
          );
        })}

        {IS_LOCAL && (
          <>
            <hr className="mx-2 my-3 hidden border-white/10 lg:block" />
            {TOOLS.map((tool) => (
              <a
                key={tool.label}
                href={tool.href}
                target="_blank"
                rel="noreferrer"
                className="flex shrink-0 items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-ink-2 transition-colors hover:bg-white/5 hover:text-ink"
              >
                <Icon name={tool.icon} className="h-5 w-5" />
                {tool.label}
                <Icon name="external" className="h-3.5 w-3.5 text-ink-muted" />
              </a>
            ))}
          </>
        )}
      </nav>
    </aside>
  );
}

function FeedControls({ state, refresh }) {
  const [rate, setRate] = useState(1);
  const [busy, setBusy] = useState(false);
  const running = Boolean(state?.running);

  useEffect(() => {
    if (state?.rate) setRate(state.rate);
  }, [state?.rate]);

  const toggle = async () => {
    setBusy(true);
    try {
      if (running) await stop();
      else await start(rate);
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const changeRate = async (next) => {
    setRate(next);
    if (running) {
      await start(next);
      refresh();
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span
        className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm ${
          running ? "border-good/30 text-good" : "border-white/10 text-ink-muted"
        }`}
      >
        <span className={`h-2 w-2 rounded-full ${running ? "bg-good" : "bg-ink-muted"}`} aria-hidden="true" />
        {running ? "Feed running" : "Feed stopped"}
      </span>

      <label className="sr-only" htmlFor="feed-rate">
        Payments each second
      </label>
      <select
        id="feed-rate"
        value={rate}
        onChange={(event) => changeRate(Number(event.target.value))}
        className="tabular rounded-lg border border-white/15 bg-surface px-3 py-1.5 text-sm text-ink"
      >
        {RATES.map((value) => (
          <option key={value} value={value}>
            {value} / sec
          </option>
        ))}
      </select>

      <button
        type="button"
        onClick={toggle}
        disabled={busy || !state}
        className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40 ${
          running
            ? "border-critical/60 text-critical hover:bg-critical/10"
            : "border-good/60 text-good hover:bg-good/10"
        }`}
      >
        <Icon name={running ? "stop" : "play"} className="h-4 w-4" />
        {running ? "Stop" : "Start"}
      </button>

      <span className="rounded-lg border border-white/10 px-3 py-1.5 text-sm text-ink-2">
        {state?.model_version ? `Model v${state.model_version} · production` : "No model"}
      </span>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState("live");
  const [accountId, setAccountId] = useState("");
  const [state, setState] = useState(null);

  const refresh = useCallback(() => {
    getState().then(setState).catch(() => setState(null));
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3_000);
    return () => clearInterval(timer);
  }, [refresh]);

  const openAccount = (id) => {
    setAccountId(id);
    setTab("account");
  };

  const current = TABS.find((option) => option.key === tab);

  return (
    <div className="min-h-full bg-plane lg:flex">
      <Sidebar tab={tab} setTab={setTab} openAlerts={state?.open_alerts ?? 0} />

      <div className="min-w-0 flex-1">
        <header className="mx-auto flex max-w-[1500px] flex-wrap items-start gap-x-6 gap-y-3 px-4 pb-2 pt-5 lg:px-6">
          <div className="min-w-[14rem] flex-1">
            <h1 className="text-2xl font-semibold tracking-tight text-ink [text-wrap:balance]">{current.title}</h1>
            <p className="mt-0.5 text-sm text-ink-muted">{current.note}</p>
          </div>
          <FeedControls state={state} refresh={refresh} />
        </header>

        <main className="mx-auto max-w-[1500px] px-4 py-4 lg:px-6">
          {tab === "live" && <Live state={state} openTab={setTab} />}
          {tab === "alerts" && <Alerts onOpenAccount={openAccount} />}
          {tab === "model" && <Model state={state} />}
          {tab === "pipeline" && <Pipeline state={state} />}
          {tab === "data" && <Data />}
          {tab === "ask" && <Ask />}
          {tab === "account" && <Account initialId={accountId} />}
        </main>

        <footer className="mx-auto flex max-w-[1500px] flex-wrap justify-between gap-2 px-4 pb-8 pt-2 text-xs text-ink-muted lg:px-6">
          <span>Synthetic data. No real customer ever appears here.</span>
          <span>finplat · Real-Time Fraud Detection</span>
        </footer>
      </div>
    </div>
  );
}
