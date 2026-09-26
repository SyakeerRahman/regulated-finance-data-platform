import { useEffect, useRef, useState } from "react";
import { Button, Panel, Stat, Table } from "../components/Chrome.jsx";
import VolumeChart from "../components/VolumeChart.jsx";
import { clock, money, start, stop } from "../api.js";

const RATES = [1, 3, 10, 50];
const BUCKET_MS = 10_000;
const BUCKETS = 30;

/** Roll the stream into 10-second buckets. A point for every transaction would be 3,000 points
 *  a minute, and an SVG chart drops frames long before that. */
function bucketise(previous, row) {
  const at = Math.floor(Date.now() / BUCKET_MS) * BUCKET_MS;
  const last = previous[previous.length - 1];
  if (last && last.at === at) {
    const updated = { ...last, scored: last.scored + 1, alerts: last.alerts + (row.alert ? 1 : 0) };
    return [...previous.slice(0, -1), updated];
  }
  const fresh = { at, label: new Date(at).toTimeString().slice(0, 8), scored: 1, alerts: row.alert ? 1 : 0 };
  return [...previous, fresh].slice(-BUCKETS);
}

export default function Live({ state, refresh }) {
  const [rows, setRows] = useState([]);
  const [buckets, setBuckets] = useState([]);
  const [running, setRunning] = useState(false);
  const [rate, setRate] = useState(1);
  const counted = useRef({ scored: 0, alerts: 0 });

  useEffect(() => {
    if (state) setRunning(state.running);
  }, [state]);

  useEffect(() => {
    const source = new EventSource("/api/stream");
    source.onmessage = (event) => {
      const row = JSON.parse(event.data);
      counted.current = {
        scored: counted.current.scored + 1,
        alerts: counted.current.alerts + (row.alert ? 1 : 0),
      };
      setRows((previous) => [row, ...previous].slice(0, 60));
      setBuckets((previous) => bucketise(previous, row));
    };
    return () => source.close();
  }, []);

  const toggle = async () => {
    const answer = running ? await stop() : await start(rate);
    setRunning(answer.running);
    refresh();
  };

  const changeRate = async (next) => {
    setRate(next);
    if (running) await start(next);
  };

  const scored = (state?.scored ?? 0) || counted.current.scored;
  const alerts = (state?.alerts ?? 0) || counted.current.alerts;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Scored" value={scored.toLocaleString()} sub="since the service started" />
        <Stat
          label="Alerts"
          value={alerts.toLocaleString()}
          tone={alerts ? "critical" : "ink"}
          sub={scored ? `${((alerts / scored) * 100).toFixed(2)}% of rows` : "none yet"}
        />
        <Stat label="Written to the lake" value={(state?.written_to_lake ?? 0).toLocaleString()} sub={`${state?.buffered ?? 0} buffered`} />
        <Stat label="Model" value={state?.model_version ? `v${state.model_version}` : "none"} sub={`alerts above ${state?.threshold ?? "-"}`} />
      </div>

      <Panel
        title="Volume"
        note="Transactions and alerts for each 10 seconds"
        right={
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full ${running ? "bg-good" : "bg-ink-muted"}`}
              aria-hidden="true"
            />
            <span className="text-xs text-ink-muted">{running ? "running" : "stopped"}</span>
            <select
              value={rate}
              onChange={(event) => changeRate(Number(event.target.value))}
              className="rounded-md border border-white/15 bg-surface px-2 py-1 text-xs text-ink"
            >
              {RATES.map((value) => (
                <option key={value} value={value}>
                  {value}/sec
                </option>
              ))}
            </select>
            <Button onClick={toggle} tone={running ? "critical" : "good"}>
              {running ? "Stop" : "Start"}
            </Button>
          </div>
        }
      >
        <VolumeChart buckets={buckets} />
      </Panel>

      <Panel title="As they arrive" note="The last 60 transactions">
        <Table
          rows={rows}
          rowKey={(row) => row.transaction_id + row.ts}
          rowClass={(row) => (row.alert ? "bg-critical/10" : "")}
          empty="Press Start."
          columns={[
            { key: "ts", label: "Time", mono: true, render: (row) => clock(row.ts) },
            { key: "account_id", label: "Account", mono: true },
            { key: "amount", label: "Amount", right: true, render: (row) => money(row.amount) },
            { key: "country", label: "Country", hideSmall: true },
            { key: "channel", label: "Channel", hideSmall: true },
            { key: "merchant_category", label: "Category", hideSmall: true },
            {
              key: "amount_vs_account",
              label: "vs account",
              right: true,
              render: (row) => `${row.amount_vs_account.toFixed(2)}x`,
            },
            {
              key: "score",
              label: "Score",
              right: true,
              render: (row) => (
                <span className={row.alert ? "font-semibold text-critical" : ""}>{row.score.toFixed(3)}</span>
              ),
            },
          ]}
        />
      </Panel>
    </div>
  );
}
