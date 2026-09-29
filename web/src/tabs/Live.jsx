import { useEffect, useMemo, useState } from "react";
import { Panel, Pill, Table } from "../components/Chrome.jsx";
import { Icon } from "../components/Icons.jsx";
import Kpi, { change } from "../components/Kpi.jsx";
import LatestAlert from "../components/LatestAlert.jsx";
import ScoreHistogram from "../components/ScoreHistogram.jsx";
import TopList from "../components/TopList.jsx";
import VolumeChart, { VolumeLegend } from "../components/VolumeChart.jsx";
import { COUNTRY_NAMES, clock, money, pretty } from "../api.js";

const FEED_ROWS = 60;

/** Six 10-second buckets make a minute. A sparkline of 360 points is noise at 96 pixels. */
function perMinute(values) {
  const minutes = [];
  for (let index = 0; index < values.length; index += 6) {
    minutes.push(values.slice(index, index + 6).reduce((sum, value) => sum + value, 0));
  }
  // The last minute is still filling, the same as the last bucket on the volume chart.
  return minutes.slice(0, -1);
}

const hhmmss = (epochSeconds) => new Date(epochSeconds * 1000).toTimeString().slice(0, 8);

export default function Live({ state, openTab }) {
  const [rows, setRows] = useState([]);
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState([]);

  useEffect(() => {
    const source = new EventSource("/api/stream");
    source.onmessage = (event) => {
      const row = JSON.parse(event.data);
      setRows((previous) => [row, ...previous].slice(0, FEED_ROWS));
    };
    return () => source.close();
  }, []);

  const live = state?.live;
  const threshold = state?.threshold ?? null;

  const buckets = useMemo(() => {
    if (!live) return [];
    // The last bucket is still filling. Drawn, it reads as traffic falling off a cliff.
    return live.scored.slice(0, -1).map((scored, index) => ({
      label: hhmmss(live.first + index * live.bucket_seconds),
      scored,
      alerts: live.alerts[index],
    }));
  }, [live]);

  const tiles = useMemo(() => {
    if (!live) return null;
    const now = live.last_hour;
    const before = live.previous_hour;
    const rate = now.scored ? now.alerts / now.scored : 0;
    const beforeRate = before?.scored ? before.alerts / before.scored : null;
    const scoredPerMinute = perMinute(live.scored);
    const alertsPerMinute = perMinute(live.alerts);
    return {
      scored: now.scored,
      alerts: now.alerts,
      rate,
      scoredDelta: change(now.scored, before?.scored),
      alertsDelta: change(now.alerts, before?.alerts, { higherIsBad: true }),
      rateDelta: change(rate, beforeRate, { higherIsBad: true, points: true }),
      scoredSpark: scoredPerMinute,
      alertsSpark: alertsPerMinute,
      rateSpark: scoredPerMinute.map((scored, index) => (scored ? alertsPerMinute[index] / scored : 0)),
      sub: before ? "vs. previous hour" : "last 60 minutes",
    };
  }, [live]);

  const togglePause = () => {
    if (!paused) setFrozen(rows);
    setPaused(!paused);
  };

  const shown = paused ? frozen : rows;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          icon="card"
          iconTone="blue"
          label="Payments scored"
          value={(tiles?.scored ?? 0).toLocaleString()}
          delta={tiles?.scoredDelta}
          sub={tiles?.sub ?? "last 60 minutes"}
          spark={tiles?.scoredSpark}
          sparkColor="var(--series-1)"
        />
        <Kpi
          icon="alert"
          iconTone="orange"
          label="Alerts raised"
          value={(tiles?.alerts ?? 0).toLocaleString()}
          delta={tiles?.alertsDelta}
          sub={tiles?.sub ?? "last 60 minutes"}
          spark={tiles?.alertsSpark}
          sparkColor="var(--series-2)"
        />
        <Kpi
          icon="bars"
          iconTone="red"
          label="Alert rate"
          value={`${((tiles?.rate ?? 0) * 100).toFixed(2)}%`}
          delta={tiles?.rateDelta}
          sub={tiles?.sub ?? "last 60 minutes"}
          spark={tiles?.rateSpark}
          sparkColor="var(--series-2)"
        />
        <Kpi
          icon="inbox"
          iconTone="grey"
          label="Open alerts"
          value={(state?.open_alerts ?? 0).toLocaleString()}
          sub="waiting for a decision"
          onClick={() => openTab("alerts")}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-12 [&>*]:min-w-0">
        <Panel
          className="xl:col-span-6"
          title="Volume, last 60 minutes"
          note="Counts for each 10 seconds"
          right={<VolumeLegend />}
        >
          <VolumeChart buckets={buckets} />
        </Panel>

        <Panel className="xl:col-span-3" title="Alerts by merchant category" note="Since the service started">
          <TopList
            rows={live?.categories ?? []}
            total={live?.alerts_total ?? 0}
            label={pretty}
            empty="No alerts yet."
          />
        </Panel>

        <Panel className="xl:col-span-3" title="Alerts by country" note="Since the service started">
          <TopList
            rows={live?.countries ?? []}
            total={live?.alerts_total ?? 0}
            label={(code) => COUNTRY_NAMES[code] ?? code}
            badge
            empty="No alerts yet."
          />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-12 [&>*]:min-w-0">
        <Panel
          className="xl:col-span-7"
          title={
            <span className="inline-flex items-center gap-2">
              <span
                className={`h-2.5 w-2.5 rounded-full ${state?.running && !paused ? "bg-good" : "bg-ink-muted"}`}
                aria-hidden="true"
              />
              Live feed
            </span>
          }
          note={paused ? "Paused. New payments are still scored" : `The last ${FEED_ROWS} payments, newest first`}
          right={
            <button
              type="button"
              onClick={togglePause}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3 py-1.5 text-sm text-ink transition-colors hover:border-white/35"
            >
              <Icon name={paused ? "play" : "pause"} className="h-4 w-4" />
              {paused ? "Resume" : "Pause"}
            </button>
          }
        >
          <div className="max-h-[560px] overflow-y-auto">
            <Table
              rows={shown}
              rowKey={(row) => row.transaction_id + row.ts}
              rowClass={(row) => (row.alert ? "bg-critical/10" : "")}
              empty="Press Start. Payments appear here as they are scored."
              columns={[
                { key: "ts", label: "Time", mono: true, render: (row) => clock(row.ts) },
                { key: "transaction_id", label: "Transaction ID", mono: true, hideSmall: true },
                { key: "account_id", label: "Account", mono: true },
                { key: "amount", label: "Amount", right: true, render: (row) => money(row.amount) },
                { key: "country", label: "Country", hideSmall: true },
                { key: "channel", label: "Channel", hideSmall: true },
                {
                  key: "score",
                  label: "Score",
                  right: true,
                  render: (row) => (
                    <span className={row.alert ? "font-semibold text-critical" : "text-ink-2"}>{row.score.toFixed(3)}</span>
                  ),
                },
                {
                  key: "status",
                  label: "Status",
                  render: (row) => (row.alert ? <Pill tone="serious">Alert</Pill> : <Pill tone="good">Pass</Pill>),
                },
              ]}
            />
          </div>
        </Panel>

        <div className="space-y-4 xl:col-span-5">
          <Panel title="Score distribution" note="Every payment since the service started">
            <ScoreHistogram histogram={live?.histogram ?? []} threshold={threshold} />
          </Panel>
          <LatestAlert />
        </div>
      </div>
    </div>
  );
}
