import { useCallback, useEffect, useMemo, useState } from "react";
import { Panel, Pill } from "../components/Chrome.jsx";
import { Icon } from "../components/Icons.jsx";
import Kpi from "../components/Kpi.jsx";
import { Legend } from "../components/PipelineCharts.jsx";
import { CurveChart, ThresholdChart, ValueBars } from "../components/ModelCharts.jsx";
import { clock, getDrift, getEvaluation, getModel, money, pretty, promoteVersion, reasonLabel, triggerDag } from "../api.js";

const DRIFT_TONE = { stable: "good", moderate: "warning", significant: "critical" };
const DRIFT_COLOR = { stable: "var(--good)", moderate: "var(--warning)", significant: "var(--critical)" };
// Below this many live rows the PSI is noise, so the panel says it is still collecting.
const DRIFT_MIN_ROWS = 1_000;

const registered = (ms) => new Date(ms).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const toPoints = (xs, ys) => xs.map((x, index) => ({ x, y: ys[index] }));

function Tabs({ options, value, onChange, label }) {
  return (
    <div className="flex gap-1 rounded-lg border border-white/10 p-0.5" role="tablist" aria-label={label}>
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={value === key}
          onClick={() => onChange(key)}
          className={`rounded-md px-2.5 py-1 text-xs transition-colors ${value === key ? "bg-series-1/15 font-medium text-series-1" : "text-ink-2 hover:text-ink"}`}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

function Metric({ label, value, previous, versionBefore }) {
  const difference = value != null && previous != null ? value - previous : null;
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
      <div className="text-xs text-ink-2">{label}</div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-2">
        <span className="tabular text-2xl font-semibold text-ink">{value != null ? value.toFixed(3) : "-"}</span>
        {difference != null && Math.abs(difference) >= 0.0005 && (
          <span className={`tabular text-xs font-medium ${difference > 0 ? "text-good" : "text-critical"}`}>
            <span aria-hidden="true">{difference > 0 ? "↑" : "↓"}</span>
            <span className="sr-only">{difference > 0 ? "up" : "down"}</span> {Math.abs(difference).toFixed(3)}
          </span>
        )}
        {difference != null && Math.abs(difference) < 0.0005 && <span className="text-xs text-ink-muted">no change</span>}
      </div>
      <div className="mt-0.5 text-[11px] text-ink-muted">{versionBefore ? `vs. v${versionBefore}` : "no earlier version"}</div>
    </div>
  );
}

export default function Model({ state }) {
  const [data, setData] = useState(null);
  const [evaluations, setEvaluations] = useState({});
  const [drift, setDrift] = useState(null);
  const [curve, setCurve] = useState("roc");
  const [view, setView] = useState("global");
  const [confirming, setConfirming] = useState("");
  const [message, setMessage] = useState(null);

  const load = useCallback(() => {
    getModel().then(setData).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const read = () => getDrift().then(setDrift).catch(() => {});
    read();
    const timer = setInterval(read, 30_000);
    return () => clearInterval(timer);
  }, [data?.live]);

  const versions = data?.versions ?? [];
  const live = versions.find((row) => row.version === data?.live);
  // The version before the live one, by number: the one it replaced or will be compared with.
  const before = versions.find((row) => Number(row.version) < Number(data?.live ?? 0));

  useEffect(() => {
    for (const row of [live, before]) {
      if (row && !evaluations[row.version]) {
        setEvaluations((previous) => ({ ...previous, [row.version]: "loading" }));
        getEvaluation(row.version)
          .then((answer) => setEvaluations((previous) => ({ ...previous, [row.version]: answer })))
          .catch(() => setEvaluations((previous) => ({ ...previous, [row.version]: null })));
      }
    }
  }, [live, before, evaluations]);

  const liveEval = live ? evaluations[live.version] : null;
  const beforeEval = before ? evaluations[before.version] : null;
  const ready = (evaluation) => evaluation && evaluation !== "loading";

  const curveSeries = useMemo(() => {
    const out = [];
    const add = (row, evaluation, color, width) => {
      if (!ready(evaluation)) return;
      const points = curve === "roc" ? toPoints(evaluation.roc.fpr, evaluation.roc.tpr) : toPoints(evaluation.pr.recall, evaluation.pr.precision);
      const score = curve === "roc" ? evaluation.roc_auc : evaluation.pr_auc;
      out.push({ name: `v${row.version} (${curve === "roc" ? "ROC-AUC" : "PR-AUC"} ${score.toFixed(3)})`, color, width, points });
    };
    add(live ?? {}, liveEval, "var(--series-1)", 2.2);
    add(before ?? {}, beforeEval, "var(--series-2)", 1.6);
    return out;
  }, [curve, live, before, liveEval, beforeEval]);

  const recomputed = [liveEval, beforeEval].some((evaluation) => ready(evaluation) && evaluation.source === "recomputed");

  const onPromote = async (version) => {
    setConfirming("");
    setMessage({ tone: "ok", text: `Promoting v${version} and loading it into the scorer.` });
    try {
      const answer = await promoteVersion(version);
      setMessage({ tone: "ok", text: `v${answer.live} is now in production, alerting above ${answer.threshold.toFixed(3)}. The scorer switched without a restart.` });
      setEvaluations({});
      load();
    } catch (error) {
      setMessage({ tone: "error", text: String(error.message ?? error) });
    }
  };

  const onTrain = async () => {
    setConfirming("");
    try {
      const answer = await triggerDag("train_fraud_model");
      setMessage({
        tone: "ok",
        text: answer.paused
          ? `Training queued (${answer.dag_run_id}). train_fraud_model is paused in Airflow, so it starts once you unpause it. A new version is registered but not promoted.`
          : `Training started (${answer.dag_run_id}). The new version is registered but not promoted.`,
      });
    } catch (error) {
      setMessage({ tone: "error", text: String(error.message ?? error) });
    }
  };

  const importanceRows =
    view === "global"
      ? (data?.importance.global ?? []).map(([feature, share]) => ({ label: reasonLabel(feature), value: share }))
      : (data?.importance.local ?? []).map(([feature, push, alerts]) => ({ label: reasonLabel(feature), value: push, alerts }));

  const live60 = state?.live;
  const recent = state?.recent ?? [];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          icon="model"
          iconTone="blue"
          label="Current model"
          value={data?.live ? `v${data.live}` : "-"}
          sub={live ? `Registered ${registered(live.created)}` : "loading"}
        />
        <Kpi
          icon="bars"
          iconTone="red"
          label="Model type"
          value={data?.type === "XGBClassifier" ? "XGBoost" : (data?.type ?? "-")}
          sub={live ? `${live.params.n_estimators} trees, depth ${live.params.max_depth}, learning rate ${live.params.learning_rate}` : "loading"}
        />
        <Kpi
          icon="live"
          iconTone="amber"
          label="Scoring time (p95)"
          value={live60?.latency_p95_ms != null ? `${live60.latency_p95_ms.toFixed(1)} ms` : "-"}
          sub="last 1,000 payments"
        />
        <Kpi
          icon="database"
          iconTone="grey"
          label="Payments scored"
          value={(state?.scored ?? 0).toLocaleString()}
          sub={`since the service started. ${(live60?.last_hour.scored ?? 0).toLocaleString()} in the last hour`}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-12 [&>*]:min-w-0">
        <Panel
          className="xl:col-span-5"
          title="Model performance"
          note={
            recomputed
              ? "Recomputed on today's lake. No curves were saved"
              : "Each version's own test set, saved at training"
          }
          right={<Tabs label="Curve" value={curve} onChange={setCurve} options={[["roc", "ROC curve"], ["pr", "Precision-recall"]]} />}
        >
          {curveSeries.length === 0 ? (
            <p className="px-4 py-24 text-center text-sm text-ink-muted">{ready(liveEval) || liveEval === null ? "No curves." : "Scoring the test set. A few seconds."}</p>
          ) : (
            <>
              <CurveChart
                series={curveSeries}
                xLabel={curve === "roc" ? "False positive rate" : "Recall"}
                yLabel={curve === "roc" ? "True positive rate" : "Precision"}
                diagonal={curve === "roc"}
              />
              <div className="px-4 pb-3">
                <Legend
                  items={[
                    ...curveSeries.map((line) => ({ label: line.name, color: line.color })),
                    ...(curve === "roc" ? [{ label: "Random guess (0.500)", color: "var(--ink-muted)", dashed: true }] : []),
                  ]}
                />
              </div>
            </>
          )}
        </Panel>

        <Panel className="xl:col-span-4" title="Key metrics" note={live ? `Logged at training, at threshold ${live.threshold?.toFixed(3)}` : ""}>
          <div className="grid grid-cols-2 gap-3 p-4">
            {[
              ["pr_auc", "PR-AUC"],
              ["precision", "Precision"],
              ["recall", "Recall"],
              ["f1", "F1 score"],
            ].map(([key, label]) => (
              <Metric key={key} label={label} value={live?.metrics[key]} previous={before?.metrics[key]} versionBefore={before?.version} />
            ))}
          </div>
          {ready(liveEval) && liveEval.source === "recomputed" && live?.metrics.pr_auc != null && (
            <p className="mx-4 mb-4 rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs leading-relaxed text-ink-2">
              <span className="font-medium text-warning">! Recomputed on today&apos;s lake: PR-AUC {liveEval.pr_auc.toFixed(3)}</span>, against{" "}
              {live.metrics.pr_auc.toFixed(3)} at training, on {liveEval.rows.toLocaleString()} test rows instead of{" "}
              {(live.metrics.test_rows ?? 0).toLocaleString()}. The lake was rebuilt after v{live.version} was trained, so this is not its original test set.
            </p>
          )}
          <p className="px-4 pb-4 text-xs leading-relaxed text-ink-muted">
            No accuracy: fraud is about 1.5% of payments, so &ldquo;never fraud&rdquo; scores 98.5%. Live accuracy is unknown until chargebacks arrive, 30 to 90 days later.
          </p>
        </Panel>

        <Panel
          className="xl:col-span-3"
          title="Feature importance"
          note={view === "global" ? "Share of the trees' total gain" : "Mean SHAP push in the latest 500 alerts"}
          right={<Tabs label="Importance" value={view} onChange={setView} options={[["global", "Global"], ["local", "Alerts"]]} />}
        >
          {importanceRows.length === 0 ? (
            <p className="px-4 py-16 text-center text-sm text-ink-muted">{view === "local" ? "No alerts yet." : "Loading."}</p>
          ) : (
            <ValueBars rows={importanceRows} format={(value) => (view === "global" ? `${(value * 100).toFixed(1)}%` : `+${value.toFixed(2)}`)} color={view === "global" ? "var(--series-1)" : "var(--series-2)"} />
          )}
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-12 [&>*]:min-w-0">
        <Panel
          className="xl:col-span-5"
          title="Model drift"
          note={drift ? `Live stream (${drift.live_rows.toLocaleString()} payments) against v${drift.version}'s training data (${drift.reference_rows.toLocaleString()} rows). PSI` : "Loading"}
        >
          {!drift ? (
            <p className="px-4 py-16 text-center text-sm text-ink-muted">Reading the training data. A few seconds the first time.</p>
          ) : drift.live_rows < DRIFT_MIN_ROWS ? (
            <p className="px-4 py-16 text-center text-sm text-ink-muted">
              Collecting live payments: {drift.live_rows.toLocaleString()} of {DRIFT_MIN_ROWS.toLocaleString()} needed. Press Start on the feed.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3 border-b border-white/5 px-4 py-3 text-sm">
                <span className="text-ink-2">Prediction drift (score)</span>
                <span className="tabular font-semibold text-ink">{drift.prediction.psi.toFixed(3)}</span>
                <Pill tone={DRIFT_TONE[drift.prediction.level]}>{pretty(drift.prediction.level)}</Pill>
                <span className="ml-auto text-xs text-ink-muted">Under 0.1 stable. 0.1 to 0.25 moderate. Over 0.25 significant</span>
              </div>
              <ValueBars
                rows={drift.features.map((row) => ({ label: reasonLabel(row.feature), value: row.psi, level: row.level, color: DRIFT_COLOR[row.level] }))}
                format={(value) => value.toFixed(3)}
                badge={(row) => <Pill tone={DRIFT_TONE[row.level]}>{pretty(row.level)}</Pill>}
              />
              <p className="px-4 pb-4 text-xs leading-relaxed text-ink-muted">
                Time of day and night time follow the clock. The feed replays one generated day but stamps each payment with the
                time it is sent, so a few minutes of feed cover one hour of the day. Account history drifts because the feed
                starts every account from what silver holds.
              </p>
            </>
          )}
        </Panel>

        <Panel
          className="xl:col-span-4"
          title="Threshold analysis"
          note={live ? `v${live.version}. Dashed red line: where it alerts now` : ""}
          right={
            <Legend
              items={[
                { label: "Precision", color: "var(--series-1)" },
                { label: "Recall", color: "var(--series-2)" },
                { label: "F1", color: "var(--ink-2)", dashed: true },
              ]}
            />
          }
        >
          {ready(liveEval) ? (
            <ThresholdChart table={liveEval.thresholds} threshold={data?.threshold} />
          ) : (
            <p className="px-4 py-24 text-center text-sm text-ink-muted">Scoring the test set. A few seconds.</p>
          )}
        </Panel>

        <Panel
          className="xl:col-span-3"
          title="Model versions"
          right={
            confirming === "train" ? (
              <span className="inline-flex gap-1.5">
                <button type="button" onClick={onTrain} className="rounded-md border border-series-1/60 px-2.5 py-1 text-xs font-medium text-series-1 hover:bg-series-1/10">
                  Start training
                </button>
                <button type="button" onClick={() => setConfirming("")} className="px-1.5 py-1 text-xs text-ink-muted hover:text-ink">
                  Cancel
                </button>
              </span>
            ) : (
              <button type="button" onClick={() => setConfirming("train")} className="inline-flex items-center gap-1.5 rounded-md border border-white/15 px-2.5 py-1 text-xs text-ink hover:border-white/35">
                + Train new model
              </button>
            )
          }
        >
          <div className="relative overflow-x-auto">
            <table className="w-full whitespace-nowrap text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-[11px] font-medium uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Version</th>
                  <th className="px-2 py-2 text-right font-medium">PR-AUC</th>
                  <th className="px-4 py-2 text-right font-medium">Stage</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((row) => {
                  const production = row.aliases.includes("production");
                  return (
                    <tr key={row.version} className="border-b border-white/5 last:border-0">
                      <td className="px-4 py-2">
                        <span className="tabular text-ink">v{row.version}</span>
                        <span className="block text-[11px] text-ink-muted">{registered(row.created)}</span>
                      </td>
                      <td className="tabular px-2 py-2 text-right">{row.metrics.pr_auc?.toFixed(3) ?? "-"}</td>
                      <td className="px-4 py-2 text-right">
                        {production ? (
                          <Pill tone="good">Production</Pill>
                        ) : confirming === row.version ? (
                          <span className="inline-flex gap-1">
                            <button type="button" onClick={() => onPromote(row.version)} className="rounded-md border border-good/60 px-2 py-0.5 text-xs font-medium text-good hover:bg-good/10">
                              Confirm
                            </button>
                            <button type="button" onClick={() => setConfirming("")} className="px-1 text-xs text-ink-muted hover:text-ink">
                              Cancel
                            </button>
                          </span>
                        ) : (
                          <button type="button" onClick={() => setConfirming(row.version)} className="rounded-md border border-white/15 px-2 py-0.5 text-xs text-ink hover:border-white/35">
                            Promote
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {message && (
            <p className={`border-t border-white/10 px-4 py-2.5 text-xs ${message.tone === "error" ? "text-critical" : "text-ink-2"}`}>{message.text}</p>
          )}
        </Panel>
      </div>

      <Panel title="Recent predictions" note="The latest payments from the live stream. SHAP runs on alerts only, so a pass has no reasons">
        <div className="relative max-h-[420px] overflow-auto">
          <table className="w-full whitespace-nowrap text-sm">
            <thead>
              <tr className="border-b border-white/10 text-left text-[11px] font-medium uppercase tracking-wide text-ink-muted">
                <th className="sticky top-0 bg-surface px-4 py-2 font-medium">Time</th>
                <th className="sticky top-0 hidden bg-surface px-2 py-2 font-medium md:table-cell">Transaction ID</th>
                <th className="sticky top-0 bg-surface px-2 py-2 font-medium">Account</th>
                <th className="sticky top-0 bg-surface px-2 py-2 text-right font-medium">Amount</th>
                <th className="sticky top-0 hidden bg-surface px-2 py-2 font-medium lg:table-cell">Category</th>
                <th className="sticky top-0 hidden bg-surface px-2 py-2 font-medium md:table-cell">Country</th>
                <th className="sticky top-0 hidden bg-surface px-2 py-2 font-medium lg:table-cell">Model</th>
                <th className="sticky top-0 bg-surface px-2 py-2 text-right font-medium">Score</th>
                <th className="sticky top-0 bg-surface px-2 py-2 font-medium">Prediction</th>
                <th className="sticky top-0 hidden bg-surface px-4 py-2 font-medium md:table-cell">Top reasons (SHAP)</th>
              </tr>
            </thead>
            <tbody>
              {recent.length === 0 && (
                <tr>
                  <td colSpan={10} className="px-4 py-10 text-center text-ink-muted">
                    Press Start. Predictions appear here.
                  </td>
                </tr>
              )}
              {recent.slice(0, 20).map((row) => (
                <tr key={row.transaction_id + row.ts} className={`border-b border-white/5 last:border-0 ${row.alert ? "bg-critical/10" : ""}`}>
                  <td className="tabular px-4 py-1.5 text-ink-2">{clock(row.ts)}</td>
                  <td className="tabular hidden px-2 py-1.5 md:table-cell">{row.transaction_id}</td>
                  <td className="tabular px-2 py-1.5">{row.account_id}</td>
                  <td className="tabular px-2 py-1.5 text-right">{money(row.amount)}</td>
                  <td className="hidden px-2 py-1.5 lg:table-cell">{pretty(row.merchant_category)}</td>
                  <td className="hidden px-2 py-1.5 md:table-cell">{row.country}</td>
                  <td className="tabular hidden px-2 py-1.5 text-ink-2 lg:table-cell">v{row.model_version}</td>
                  <td className={`tabular px-2 py-1.5 text-right ${row.alert ? "font-semibold text-critical" : "text-ink-2"}`}>{row.score.toFixed(3)}</td>
                  <td className="px-2 py-1.5">{row.alert ? <Pill tone="serious">Alert</Pill> : <Pill tone="good">Pass</Pill>}</td>
                  <td className="hidden px-4 py-1.5 md:table-cell">
                    {row.alert && row.contributions ? (
                      <span className="flex gap-1.5">
                        {Object.entries(row.contributions)
                          .sort((a, b) => b[1] - a[1])
                          .map(([feature, value]) => (
                            <span key={feature} className={`rounded border px-1.5 py-px text-[11px] ${value > 0 ? "border-series-2/40 text-series-2" : "border-white/15 text-ink-muted"}`}>
                              {value > 0 ? "+" : "-"} {reasonLabel(feature)}
                            </span>
                          ))}
                      </span>
                    ) : (
                      <span className="text-ink-muted">-</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
