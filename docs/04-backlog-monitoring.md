# Backlog: model and data monitoring with Prometheus and Grafana

Status: **approved on 2026-10-05, in Jira as Epic SCRUM-43 with SCRUM-44 to SCRUM-48.** See the decisions at the end.
Written 2026-10-05. Component in Jira: `regulated-finance-data-platform`.

## Epic

**A person sees how the model, the data and the service behaved over time, and gets an alert when one goes wrong.**

Today the dashboard shows the current state only. Three things are missing:

1. No history. The drift PSI, the alert rate and the quality results of last Tuesday are gone.
2. No alert. A person learns of a problem only when they open the dashboard.
3. No common place for the numbers. Drift is in `/api/model/drift`, quality in `/api/quality`,
   the AI budget in memory.

This epic adds a `/metrics` endpoint to the API, Prometheus to store the numbers, and Grafana to
show them and raise alerts. Both are in the locked stack in `AGENTS.md`. The dashboard and the
alert rules are files in the repo, not clicks in a UI.

Estimate: about 11 hours, or 1.5 weekends.

## Stories

| # | Jira | Type | Title | Hours | Depends on |
|---|---|---|---|---|---|
| 1 | SCRUM-44 | Story | The API publishes its numbers at /metrics | 3 | - |
| 2 | SCRUM-45 | Story | Prometheus and Grafana run in both stacks, inside the budget | 3 | 1 |
| 3 | SCRUM-46 | Story | One Grafana dashboard shows the model, the data and the service | 2 | 2 |
| 4a | SCRUM-49 | Story | The drift check compares like with like | 3 | 1 |
| 4 | SCRUM-47 | Story | Grafana raises an alert when the model, the data or the service goes wrong | 2 | 3, 4a |
| 5 | SCRUM-48 | Task | Record the decision and update the docs | 1 | 1 to 4 |

### 1. The API publishes its numbers at /metrics

- Add `GET /metrics` in the Prometheus text format.
- Model: `finplat_scored_total`, `finplat_alerts_total`, `finplat_score_latency_ms` (p50, p95,
  p99, from `LiveStats`), `finplat_drift_psi` with a `feature` label, and one for the score.
  The model version is a label.
- Data: `finplat_quality_check_passed` with `check` and `severity` labels, for the newest batch.
  `finplat_last_batch_age_hours`: hours since the newest quality result.
- Service: `finplat_open_alerts`, `finplat_llm_calls_used` and `finplat_llm_calls_limit`.
- The drift PSI is cached for 60 s. A scrape every 15 s must not compute it each time.
- No new metric reads the lake on each scrape. Read what the API already holds, or cache.

Acceptance:
- A test parses the `/metrics` output and finds each metric above.
- A test proves the drift value comes from the cache on a second scrape.
- `/metrics` answers in under 200 ms with the feed running at 50 a second.

### 2. Prometheus and Grafana run in both stacks, inside the budget

- Add `prom/prometheus` and `grafana/grafana` to `docker-compose.yml` and `deploy/compose.yml`.
- Ports on 127.0.0.1 only: Grafana 8098, Prometheus 8099. On the server, reach them with the SSH
  tunnel, as for MLflow.
- Prometheus keeps 15 days: `--storage.tsdb.retention.time=15d`. It scrapes the API every 15 s.
- Memory limits in `deploy/compose.yml`: Prometheus 256m, Grafana 256m.
- Grafana reads its data source from a provisioning file in `deploy/grafana/`.
- Named volumes for both, so a restart keeps the history.

Acceptance:
- On fresh volumes, `docker compose up -d` shows the API target as up in Prometheus.
- The measured memory and image sizes are in the commit message. The disk total stays under 8 GB.

### 3. One Grafana dashboard shows the model, the data and the service

- The dashboard is a JSON file in `deploy/grafana/dashboards/`, loaded by provisioning.
- Model row: drift PSI per feature with lines at 0.1 and 0.25, the alert rate, the score latency.
- Data row: the quality checks of the newest batch, and the age of the last batch.
- Service row: open alerts, and AI calls used against the limit.

Acceptance:
- A screenshot of the dashboard after one live feed and one daily run.
- A change to the JSON file shows in Grafana after a restart, with no click in the UI.

### 4. Grafana raises an alert when the model, the data or the service goes wrong

The rules are files in `deploy/grafana/alerting/`:

| Rule | Condition |
|---|---|
| Drift | Any feature PSI at or above 0.25 for 10 minutes |
| No batch | Last batch age above 26 hours |
| Quality gate | A critical check failed on the newest batch |
| API down | The API target is down for 2 minutes |

- The thresholds come from `PSI_SIGNIFICANT` and the freshness check, not new numbers.

Acceptance:
- Each rule fires in a rehearsal. The quality gate rule uses the rehearsal already in `CLAUDE.md`.
- The screenshot of each firing alert is in the PR.

### 5. Record the decision and update the docs

- A new decision record in `brain/decisions/`: why Prometheus and Grafana, why `/metrics` is
  written by hand or with a library (see open question 1), and the measured cost.
- `README.md`: the Grafana URL, the 4 alert rules, and how to rehearse each.
- `CLAUDE.md`: the commands and the rules for a new metric.

## Open questions, ranked

1. **Write `/metrics` by hand, or add `prometheus-client`?** The text format for gauges and
   counters is about 20 lines. The latency percentiles already come from `LiveStats`, so no
   histogram is needed. Recommendation: by hand. `AGENTS.md` says a library only when the
   alternative is more than 30 lines.
2. **Where does an alert go?** Grafana can send email, Telegram or a webhook. Locally, the alert
   shows in the Grafana UI only. Recommendation: the UI only in this epic. Choose the channel
   with the VPS and Cloudflare work.
3. **Does this close "send an alert when the platform stops" from stage F?** Partly. Grafana runs
   on the same server, so if the whole server stops, Grafana stops too. A full answer needs a
   check from outside the server, for example a free uptime service. Recommendation: keep stage F
   item 6 open, and add the outside check with the VPS.

## Assumptions I made

- Prometheus and Grafana together use about 150 to 350 MB of memory and about 1 GB of disk for
  the images. These are typical figures, not measured here. Story 2 measures them.
- The stack today uses about 5.6 GB of the 8 GB disk budget, measured on 2026-09-27.
- Ports 8098 and 8099 are free. 8095, 8096 and 8097 are in use.
- Airflow's own metrics need a StatsD exporter, one more container. This epic leaves them out.
  The quality results and the batch age cover the pipeline.

## Tickets gate decisions

Approved by the owner on 2026-10-05. All 3 recommendations above are accepted:

1. `/metrics` is written by hand. No `prometheus-client`.
2. Alerts show in the Grafana UI only in this epic. The channel is chosen with the VPS.
3. Stage F item 6 stays open. It needs a check from outside the server, added with the VPS.

## Change to the plan, during SCRUM-45

Decided by the owner on 2026-10-05, after a measurement.

| | Planned | Measured |
|---|---|---|
| Grafana image | about 0.5 GB | about 1.5 GB unpacked (`grafana/grafana:13.2.3`) |
| Grafana memory | 256m limit | 243 MiB settled under a 512m cap. Under 256m it did not finish starting in 3.5 minutes |
| Prometheus | about 0.3 GB, 150 MB | about 0.27 GB unpacked, 124 MiB |
| Alertmanager | not planned | about 0.08 GB unpacked, 12 MiB |

With Grafana the server images reach about 7.3 GB of the 8 GB budget before any data, and the
lake alone grows to about 0.53 GB. The slim Grafana image has no Prometheus data source, and
installing the plugin at startup failed.

The plan now:

- **The server runs Prometheus and Alertmanager. Grafana runs on this PC only.** The server images
  grow by about 0.35 GB, to about 6 GB.
- **The alert rules move from Grafana to Prometheus**, in `deploy/prometheus/rules.yml`, so the
  server alerts without Grafana. Alertmanager has no channel yet: an alert shows in its UI, and
  in Grafana on this PC.
- **The dashboard stays a file in `deploy/grafana/`.** It is the same wherever Grafana runs.

Story 2, SCRUM-45: Grafana leaves `deploy/compose.yml`, and Alertmanager joins both stacks.
Story 4, SCRUM-47: the rules are Prometheus rules, not Grafana rules. The four conditions stay.

## Story added on 2026-10-05: 4a. The drift check compares like with like (SCRUM-49)

Found in SCRUM-44 and SCRUM-46: on a calm day the PSI read is_night 11.4, hour 8.7,
prior_transactions 8.6, score 1.9 and amount_vs_account 1.8. A drift rule at 0.25 would fire all
the time. Approved at the tickets gate the same day. It blocks SCRUM-47.

Proved with the live model, the live reference and the live feed, one change at a time:

| Run | is_night | hour | prior_transactions | amount_vs_account | score |
|---|---|---|---|---|---|
| As the API did it | 11.4 | 8.7 | 8.6 | 1.8 | 0.95 |
| The feed's own times | under 0.05 | 0.05 | 8.6 | 1.8 | 0.17 |
| Own times, no warm history | under 0.05 | 0.05 | 1.05 | 0.89 | under 0.05 |

The two causes and their fixes:

- **The clock.** The feed drew rows from the whole day and stamped the current time on each, so a
  daytime payment was flagged as night. The feed now serves rows of the current UTC hour, and the
  drift check weights the training rows to the hours the live sample covers.
- **History grows by design.** Model v3 trained on 2.5 days, when most accounts had 0 to 2 earlier
  payments. Live accounts carry 12 days, a median of 4. `prior_transactions` and
  `amount_vs_account` are marked "by design" with a reason. They stay on the dashboard and leave
  the drift alert. They are also where a stale model shows first.

Live after the fix, at 05:39 UTC, a night hour: hour and is_night 0.000, amount 0.027, score 0.178.

Left open:

- `psi` cannot see a shift from a reference with one value: one bin holds everything. A feature
  that is constant in training reads 0.0 whatever arrives live.
- The feed sleeps `1/rate` after each payment, so rate 50 gives about 37 a second at night. The
  refill of a night hour costs about 0.19 s for each 240 rows.
