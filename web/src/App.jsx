import { useCallback, useEffect, useState } from "react";
import Live from "./tabs/Live.jsx";
import Alerts from "./tabs/Alerts.jsx";
import Model from "./tabs/Model.jsx";
import Pipeline from "./tabs/Pipeline.jsx";
import Account from "./tabs/Account.jsx";
import { getState } from "./api.js";

const TABS = [
  { key: "live", label: "Live" },
  { key: "alerts", label: "Alerts" },
  { key: "model", label: "Model" },
  { key: "pipeline", label: "Pipeline" },
  { key: "account", label: "Account" },
];

export default function App() {
  const [tab, setTab] = useState("live");
  const [state, setState] = useState(null);

  const refresh = useCallback(() => {
    getState().then(setState).catch(() => setState(null));
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5_000);
    return () => clearInterval(timer);
  }, [refresh]);

  return (
    <div className="min-h-full bg-plane">
      <header className="sticky top-0 z-10 border-b border-white/10 bg-plane/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <h1 className="text-sm font-semibold tracking-tight text-ink">
            finplat <span className="font-normal text-ink-muted">fraud platform</span>
          </h1>
          <nav className="flex gap-1">
            {TABS.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setTab(option.key)}
                className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                  tab === option.key ? "bg-white/10 text-ink" : "text-ink-muted hover:text-ink-2"
                }`}
              >
                {option.label}
              </button>
            ))}
          </nav>
          <span className="ml-auto flex items-center gap-2 text-xs text-ink-muted">
            <span
              className={`h-2 w-2 rounded-full ${state?.running ? "bg-good" : "bg-ink-muted"}`}
              aria-hidden="true"
            />
            {state?.running ? "feed running" : "feed stopped"}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-4">
        {tab === "live" && <Live state={state} refresh={refresh} />}
        {tab === "alerts" && <Alerts />}
        {tab === "model" && <Model />}
        {tab === "pipeline" && <Pipeline />}
        {tab === "account" && <Account />}
      </main>

      <footer className="mx-auto max-w-7xl px-4 pb-8 pt-2 text-xs text-ink-muted">
        Synthetic data. No real customer ever appears here.
      </footer>
    </div>
  );
}
