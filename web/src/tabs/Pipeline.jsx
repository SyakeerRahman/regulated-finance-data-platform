import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Panel, Pill } from "../components/Chrome.jsx";
import { Icon } from "../components/Icons.jsx";
import Kpi, { change } from "../components/Kpi.jsx";
import QualityGrid from "../components/QualityGrid.jsx";
import { FlowLegend, Lane } from "../components/PipelineFlow.jsx";
import { Legend, LatencyChart, VolumeChart } from "../components/PipelineCharts.jsx";
import { age, bytes, clock, getPipeline, getQuality, triggerDag } from "../api.js";

const REFRESH_MS = 10_000;
// A daily job that has not written for this long has missed at least one run.
const STALE_HOURS = 36;
const IS_LOCAL = ["localhost", "127.0.0.1"].includes(window.location.hostname);

const RUN_STATE = {
  success: { tone: "good", label: "Success" },
  failed: { tone: "critical", label: "Failed" },
  upstream_failed: { tone: "critical", label: "Upstream failed" },
  running: { tone: "info", label: "Running" },
  queued: { tone: "warning", label: "Queued" },
  scheduled: { tone: "warning", label: "Scheduled" },
  skipped: { tone: "muted", label: "Skipped" },
};
const runPill = (state) => RUN_STATE[state] ?? { tone: "muted", label: state ?? "Never run" };

const EVENT_LOOK = {
  ok: { glyph: "✓", className: "text-good" },
  warning: { glyph: "!", className: "text-warning" },
  error: { glyph: "✕", className: "text-critical" },
};

const seconds = (value) => (value == null ? "-" : value < 90 ? `${value.toFixed(0)} s` : `${(value / 60).toFixed(1)} min`);
const hhmmss = (epochSeconds) => new Date(epochSeconds * 1000).toTimeString().slice(0, 8);
const hoursSince = (iso) => (iso ? (Date.now() - new Date(iso).getTime()) / 3_600_000 : Infinity);

function perMinute(values, combine) {
  const minutes = [];
  for (let index = 0; index < values.length; index += 6) minutes.push(combine(values.slice(index, index + 6)));
  return minutes.slice(0, -1);
}
const total = (values) => values.reduce((sum, value) => sum + value, 0);

function Jobs({ airflow, onTriggered }) {
  const [open, setOpen] = useState({});
  const [confirming, setConfirming] = useState("");
  const [message, setMessage] = useState(null);

  const run = async (dagId) => {
    setConfirming("");
    try {
      const answer = await triggerDag(dagId);
      setMessage({
        tone: "ok",
        text: answer.paused
          ? `Queued ${answer.dag_run_id}. ${dagId} is paused, so the run starts once you unpause it in Airflow.`
          : `Started ${answer.dag_run_id}.`,
      });
      onTriggered();
    } catch (error) {
      setMessage({ tone: "error", text: String(error.message ?? error) });
    }
  };

  if (!airflow) return <p className="px-4 py-10 text-center text-sm text-ink-muted">Loading.</p>;
  if (!airflow.reachable) {
    return <p className="px-4 py-10 text-center text-sm text-critical">Airflow did not answer: {airflow.error}</p>;
  }

  return (
    <>
      <div className="relative overflow-x-auto">
        <table className="w-full whitespace-nowrap text-sm">
          <thead>
            <tr className="border-b border-white/10 text-left text-[11px] font-medium uppercase tracking-wide text-ink-muted">
              <th className="px-4 py-2 font-medium">DAG / task</th>
              <th className="hidden px-2 py-2 font-medium md:table-cell">Schedule</th>
              <th className="px-2 py-2 font-medium">Last run</th>
              <th className="px-2 py-2 text-right font-medium">Duration</th>
              <th className="px-2 py-2 font-medium">Status</th>
              <th className="hidden px-2 py-2 font-medium min-[1700px]:table-cell">Next run</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {airflow.dags.map((dag) => {
              const pill = runPill(dag.latest?.state);
              const expanded = open[dag.dag_id];
              return (
                <Fragment key={dag.dag_id}>
                  <tr className="border-b border-white/5">
                    <td className="px-4 py-2">
                      <button
                        type="button"
                        onClick={() => setOpen({ ...open, [dag.dag_id]: !expanded })}
                        disabled={!dag.tasks.length}
                        aria-expanded={expanded ? "true" : "false"}
                        className="inline-flex items-center gap-2 text-ink disabled:cursor-default"
                      >
                        <span className={`w-3 text-ink-muted transition-transform ${expanded ? "rotate-90" : ""}`} aria-hidden="true">
                          {dag.tasks.length ? "›" : ""}
                        </span>
                        {dag.dag_id}
                      </button>
                      {dag.paused && (
                        <span className="ml-2">
                          <Pill tone="muted">Paused</Pill>
                        </span>
                      )}
                    </td>
                    <td className="hidden px-2 py-2 text-ink-2 md:table-cell">{dag.schedule}</td>
                    <td className="tabular px-2 py-2 text-ink-2">{dag.latest?.start ? age(dag.latest.start) : "Never"}</td>
                    <td className="tabular px-2 py-2 text-right text-ink-2">{seconds(dag.latest?.duration)}</td>
                    <td className="px-2 py-2">
                      <Pill tone={pill.tone}>{pill.label}</Pill>
                    </td>
                    <td className="tabular hidden px-2 py-2 text-ink-2 min-[1700px]:table-cell">
                      {dag.paused ? "Paused" : dag.next_run ? new Date(dag.next_run).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "-"}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {confirming === dag.dag_id ? (
                        <span className="inline-flex gap-1.5">
                          <button type="button" onClick={() => run(dag.dag_id)} className="rounded-md border border-series-1/60 px-2.5 py-1 text-xs font-medium text-series-1 hover:bg-series-1/10">
                            Start run
                          </button>
                          <button type="button" onClick={() => setConfirming("")} className="px-1.5 py-1 text-xs text-ink-muted hover:text-ink">
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button type="button" onClick={() => setConfirming(dag.dag_id)} className="inline-flex items-center gap-1.5 rounded-md border border-white/15 px-2.5 py-1 text-xs text-ink hover:border-white/35">
                          <Icon name="play" className="h-3.5 w-3.5" />
                          Run now
                        </button>
                      )}
                    </td>
                  </tr>
                  {expanded &&
                    dag.tasks.map((task) => {
                      const taskPill = runPill(task.state);
                      return (
                        <tr key={task.task_id} className="border-b border-white/5 bg-white/[0.02] text-xs">
                          <td className="py-1.5 pl-12 pr-2 text-ink-2">{task.task_id}</td>
                          <td className="hidden md:table-cell" />
                          <td className="tabular px-2 py-1.5 text-ink-muted">{task.start ? clock(task.start) : "-"}</td>
                          <td className="tabular px-2 py-1.5 text-right text-ink-muted">{seconds(task.duration)}</td>
                          <td className="px-2 py-1.5">
                            <Pill tone={taskPill.tone}>{taskPill.label}</Pill>
                          </td>
                          <td className="hidden min-[1700px]:table-cell" />
                          <td />
                        </tr>
                      );
                    })}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {message && (
        <p className={`border-t border-white/10 px-4 py-2.5 text-xs ${message.tone === "error" ? "text-critical" : "text-ink-2"}`}>{message.text}</p>
      )}
    </>
  );
}

export default function Pipeline({ state }) {
  const [data, setData] = useState(null);
  const [quality, setQuality] = useState({ batches: [], checks: [], results: [] });
  const [qualityError, setQualityError] = useState("");
  const [stage, setStage] = useState("");

  const load = useCallback(() => {
    getPipeline().then(setData).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    getQuality()
      .then(setQuality)
      .catch((reason) => setQualityError(String(reason.message ?? reason)));
  }, []);

  const live = state?.live;
  const lake = useMemo(() => Object.fromEntries((data?.lake ?? []).map((row) => [row.table, row])), [data]);
  const health = useMemo(() => Object.fromEntries((data?.health ?? []).map((row) => [row.name, row])), [data]);

  const series = useMemo(() => {
    if (!live) return { volume: [], latency: [] };
    // The last bucket is still filling, the same as on the Live tab.
    const count = live.scored.length - 1;
    let ingested = 0;
    let written = 0;
    const volume = [];
    const latency = [];
    for (let index = 0; index < count; index += 1) {
      const label = hhmmss(live.first + index * live.bucket_seconds);
      ingested += live.scored[index];
      written += live.written[index];
      volume.push({ label, ingested, written });
      latency.push({ label, p50: live.latency.p50[index], p95: live.latency.p95[index], p99: live.latency.p99[index] });
    }
    return { volume, latency };
  }, [live]);

  // The newest batch's checks decide the quality gate's state.
  const lastBatch = quality.batches[quality.batches.length - 1];
  const lastChecks = quality.results.filter((row) => row.batch_id === lastBatch);
  const criticalFail = lastChecks.some((row) => !row.passed && row.severity === "critical");
  const warningFail = lastChecks.some((row) => !row.passed && row.severity !== "critical");

  const bronze = lake["bronze/transactions"];
  const silver = lake["silver/transactions"];
  const gold = lake["gold/transaction_features"];
  const quarantine = lake["silver/quarantine"];
  const daily = data?.airflow?.dags?.find((dag) => dag.dag_id === "transactions_to_delta");
  const feed = data?.feed;
  const lakeError = (live?.events ?? []).find((event) => event.stage === "lake")?.level === "error";

  const stale = (row) => hoursSince(row?.last_write) > STALE_HOURS;
  const fromHealth = (name) => (health[name] ? (health[name].status === "healthy" ? "healthy" : "error") : "idle");
  const healthWord = (name, ok = "OK") => (health[name] ? (health[name].status === "healthy" ? ok : "Down") : "Checking");

  const liveLane = [
    {
      title: "Live feed",
      icon: "live",
      status: feed?.running ? "healthy" : "warning",
      statusText: feed?.running ? "Running" : "Stopped",
      lines: [`${feed?.rate ?? "-"} payments / sec`, "Replays one generated day"],
    },
    {
      title: "Scoring",
      icon: "model",
      status: fromHealth("Scoring service"),
      statusText: healthWord("Scoring service"),
      lines: [`Model v${state?.model_version ?? "-"}`, live?.latency_p95_ms != null ? `p95 ${live.latency_p95_ms.toFixed(1)} ms` : "p95 -"],
    },
    {
      title: "Alerts",
      icon: "bell",
      status: fromHealth("Postgres"),
      statusText: healthWord("Postgres"),
      lines: [`${(live?.last_hour.alerts ?? 0).toLocaleString()} raised in the last hour`, `${(state?.open_alerts ?? 0).toLocaleString()} open, in Postgres`],
    },
    {
      title: "Stream",
      icon: "stream",
      status: "healthy",
      statusText: "OK",
      lines: [`${feed?.listeners ?? 0} ${feed?.listeners === 1 ? "browser" : "browsers"} connected`, "Server-sent events"],
    },
    {
      title: "Bronze",
      icon: "database",
      status: lakeError ? "error" : fromHealth("Delta lake"),
      statusText: lakeError ? "Write failed" : healthWord("Delta lake"),
      lines: [`${(live?.last_hour.written ?? 0).toLocaleString()} rows written this hour`, `${(feed?.buffered ?? 0).toLocaleString()} waiting in the buffer`],
    },
  ];

  const dailyLane = [
    {
      title: "Airflow",
      icon: "airflow",
      status: !data?.airflow?.reachable ? "error" : daily?.latest?.state === "failed" ? "error" : daily?.paused ? "warning" : "healthy",
      statusText: !data?.airflow?.reachable ? "Down" : daily?.latest?.state === "failed" ? "Failed" : daily?.paused ? "Paused" : "OK",
      lines: [daily?.schedule ?? "-", daily?.latest?.start ? `Last run ${age(daily.latest.start)}` : "Never run in Airflow"],
    },
    {
      title: "Silver",
      icon: "database",
      status: silver?.exists ? (stale(silver) ? "warning" : "healthy") : "error",
      statusText: silver?.exists ? (stale(silver) ? "Stale" : "OK") : "Missing",
      lines: [`${(silver?.rows ?? 0).toLocaleString()} rows`, silver?.last_write ? `Written ${age(silver.last_write)}` : "-"],
    },
    {
      title: "Quality gate",
      icon: "shield",
      status: !lastBatch ? "idle" : criticalFail ? "error" : warningFail ? "warning" : "healthy",
      statusText: !lastBatch ? "No checks" : criticalFail ? "Blocked" : warningFail ? "Warning" : "Passed",
      lines: [`${lastChecks.filter((row) => row.passed).length} of ${lastChecks.length} checks passed`, lastBatch ? `Batch ${lastBatch}` : "-"],
    },
    {
      title: "Gold",
      icon: "database",
      status: gold?.exists ? (stale(gold) ? "warning" : "healthy") : "error",
      statusText: gold?.exists ? (stale(gold) ? "Stale" : "OK") : "Missing",
      lines: [`${(gold?.rows ?? 0).toLocaleString()} feature rows`, gold?.last_write ? `Written ${age(gold.last_write)}` : "-"],
    },
    {
      title: "MLflow",
      icon: "bars",
      status: fromHealth("MLflow"),
      statusText: healthWord("MLflow"),
      lines: [`Model v${state?.model_version ?? "-"} in production`, "Retrained on Sundays"],
    },
  ];

  const scoredPerMinute = live ? perMinute(live.scored, total) : [];
  const writtenPerMinute = live ? perMinute(live.written, total) : [];
  const p95PerMinute = live ? perMinute(live.latency.p95, (values) => Math.max(0, ...values.filter((value) => value != null))) : [];
  const quarantinePct = quarantine?.latest_batch && bronze?.latest_batch ? (quarantine.latest_batch.rows / bronze.latest_batch.rows) * 100 : null;

  const airflowEvents = (data?.airflow?.dags ?? []).flatMap((dag) =>
    dag.tasks
      .filter((task) => task.end || task.start)
      .map((task) => ({
        at: new Date(task.end ?? task.start).getTime() / 1000,
        stage: "airflow",
        level: ["failed", "upstream_failed"].includes(task.state) ? "error" : task.state === "success" ? "ok" : "warning",
        text: `${dag.dag_id}.${task.task_id}: ${runPill(task.state).label.toLowerCase()} in ${seconds(task.duration)}`,
      })),
  );
  const events = [...(live?.events ?? []), ...airflowEvents].sort((a, b) => b.at - a.at);
  const stages = [...new Set(events.map((event) => event.stage))].sort();
  const shownEvents = events.filter((event) => !stage || event.stage === stage).slice(0, 12);

  const lakeRows = data?.lake ?? [];
  const lakeSize = lakeRows.reduce((sum, row) => sum + (row.size_bytes ?? 0), 0);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          icon="database"
          iconTone="blue"
          label="Payments ingested"
          value={(live?.last_hour.scored ?? 0).toLocaleString()}
          delta={live?.previous_hour ? change(live.last_hour.scored, live.previous_hour.scored) : null}
          sub={live?.previous_hour ? "vs. previous hour" : "last 60 minutes"}
          spark={scoredPerMinute}
          sparkColor="var(--series-1)"
        />
        <Kpi
          icon="live"
          iconTone="orange"
          label="Scoring time (p95)"
          value={live?.latency_p95_ms != null ? `${live.latency_p95_ms.toFixed(1)} ms` : "-"}
          sub="last 1,000 payments"
          spark={p95PerMinute}
          sparkColor="var(--series-2)"
        />
        <Kpi
          icon="pipeline"
          iconTone="grey"
          label="Written to the lake"
          value={(live?.last_hour.written ?? 0).toLocaleString()}
          sub={`last 60 minutes. ${(feed?.buffered ?? 0).toLocaleString()} in the buffer`}
          spark={writtenPerMinute}
          sparkColor="var(--ink-2)"
        />
        <Kpi
          icon="alert"
          iconTone="red"
          label="Rows quarantined"
          value={(quarantine?.latest_batch?.rows ?? 0).toLocaleString()}
          sub={quarantine?.latest_batch ? `batch ${quarantine.latest_batch.id}, ${quarantinePct?.toFixed(2) ?? "-"}% of its rows` : "no batch yet"}
        />
      </div>

      <Panel title="Pipeline overview" note="Every box is read from the running system. Nothing here is a placeholder." right={<FlowLegend />}>
        <div className="space-y-6 p-4">
          <Lane name="Live path" schedule="Always on, in the API" nodes={liveLane} />
          <Lane name="Batch path" schedule="Airflow, daily. Training weekly" nodes={dailyLane} />
        </div>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-12 [&>*]:min-w-0">
        <div className="space-y-4 xl:col-span-7">
          <Panel
            title="Pipeline jobs (Airflow)"
            note="The latest run of each DAG. Open a DAG to see its tasks."
            right={
              IS_LOCAL && (
                <a href="http://localhost:8095" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-md border border-series-1/50 px-2.5 py-1 text-xs text-series-1 hover:bg-series-1/10">
                  Open in Airflow
                  <Icon name="external" className="h-3.5 w-3.5" />
                </a>
              )
            }
          >
            <Jobs airflow={data?.airflow} onTriggered={load} />
          </Panel>

          <Panel
            title="Recent pipeline events"
            note="What the service and Airflow did, newest first"
            right={
              <>
                <label htmlFor="event-stage" className="sr-only">
                  Stage
                </label>
                <select id="event-stage" value={stage} onChange={(event) => setStage(event.target.value)} className="rounded-lg border border-white/15 bg-surface px-2.5 py-1.5 text-xs text-ink">
                  <option value="">All stages</option>
                  {stages.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </>
            }
          >
            {shownEvents.length === 0 ? (
              <p className="px-4 py-10 text-center text-sm text-ink-muted">No events yet.</p>
            ) : (
              <ul className="divide-y divide-white/5 text-sm">
                {shownEvents.map((event, index) => {
                  const look = EVENT_LOOK[event.level] ?? EVENT_LOOK.ok;
                  return (
                    <li key={`${event.at}-${index}`} className="grid grid-cols-[4.5rem_4.5rem_1rem_1fr] items-baseline gap-2 px-4 py-2">
                      <span className="tabular text-ink-muted">{hhmmss(event.at)}</span>
                      <span className="truncate text-ink-2">{event.stage}</span>
                      <span className={look.className} aria-label={event.level}>
                        {look.glyph}
                      </span>
                      <span className="min-w-0 text-ink">{event.text}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </Panel>

          <Panel title="Lake tables" note={`Read from the Delta log. ${bytes(lakeSize)} in total`}>
            <div className="relative overflow-x-auto">
              <table className="w-full whitespace-nowrap text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-left text-[11px] font-medium uppercase tracking-wide text-ink-muted">
                    <th className="px-4 py-2 font-medium">Table</th>
                    <th className="px-2 py-2 text-right font-medium">Rows</th>
                    <th className="px-2 py-2 text-right font-medium">Files</th>
                    <th className="px-2 py-2 text-right font-medium">Size</th>
                    <th className="hidden px-2 py-2 text-right font-medium sm:table-cell">Version</th>
                    <th className="px-4 py-2 font-medium">Last write</th>
                  </tr>
                </thead>
                <tbody>
                  {lakeRows.map((row) => (
                    <tr key={row.table} className="border-b border-white/5 last:border-0">
                      <td className="tabular px-4 py-1.5 text-ink">{row.table}</td>
                      {row.exists ? (
                        <>
                          <td className="tabular px-2 py-1.5 text-right">{row.rows.toLocaleString()}</td>
                          <td className="tabular px-2 py-1.5 text-right text-ink-2">{row.files.toLocaleString()}</td>
                          <td className="tabular px-2 py-1.5 text-right text-ink-2">{bytes(row.size_bytes)}</td>
                          <td className="tabular hidden px-2 py-1.5 text-right text-ink-2 sm:table-cell">{row.version}</td>
                          <td className={`tabular px-4 py-1.5 ${stale(row) ? "text-warning" : "text-ink-2"}`}>
                            {row.last_write ? age(row.last_write) : "-"}
                            {stale(row) && <span className="sr-only"> (stale)</span>}
                          </td>
                        </>
                      ) : (
                        <td colSpan={5} className="px-2 py-1.5 text-critical">
                          Missing
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>

        <div className="space-y-4 xl:col-span-5">
          <Panel
            title="Data volume, last hour"
            note="Running totals. The gap is the buffer"
            right={<Legend items={[{ label: "Ingested", color: "var(--series-1)" }, { label: "Written to lake", color: "var(--series-2)" }]} />}
          >
            <VolumeChart points={series.volume} />
          </Panel>

          <Panel
            title="Scoring time"
            note="Features, model and SHAP, for each 10 seconds"
            right={
              <Legend
                items={[
                  { label: "p50", color: "var(--series-1)" },
                  { label: "p95", color: "var(--series-2)" },
                  { label: "p99", color: "var(--series-2)", dashed: true },
                ]}
              />
            }
          >
            <LatencyChart points={series.latency} />
          </Panel>

          <Panel title={<span className="inline-flex items-center gap-2"><Icon name="heart" className="h-4 w-4 text-good" />System health</span>} note="Checked every 10 seconds">
            {!data ? (
              <p className="px-4 py-10 text-center text-sm text-ink-muted">Checking.</p>
            ) : (
              <ul className="divide-y divide-white/5 text-sm">
                {data.health.map((row) => (
                  <li key={row.name} className="grid grid-cols-[1fr_auto_4.5rem] items-center gap-3 px-4 py-2">
                    <span className="min-w-0">
                      <span className="block text-ink">{row.name}</span>
                      <span className="block truncate text-xs text-ink-muted" title={row.detail}>
                        {row.detail}
                      </span>
                    </span>
                    <Pill tone={row.status === "healthy" ? "good" : "critical"}>{row.status === "healthy" ? "Healthy" : "Down"}</Pill>
                    <span className="tabular text-right text-xs text-ink-muted">{row.ms} ms</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>

      <QualityGrid data={quality} error={qualityError} />
    </div>
  );
}
