# Brief

Last updated 2026-09-26. This version replaces the three-weekend brief of 2026-09-22.

## The problem

Nine job postings for data platform roles in regulated finance ask for Kubernetes, MLflow,
AWS Lambda, CI/CD, and Delta Lake. No public repository in the portfolio shows these tools.

The first version of this brief stopped at a batch pipeline that a reader must clone and run.
Very few readers clone anything. This version builds a fraud platform that stays online, and
gives the reader a link instead of a repository.

## The reader

A hiring manager or an engineer who reviews the portfolio. The reader has 5 minutes and often
looks on a phone. The reader must see each tool do real work. The reader must not install
anything.

## Done when

1. A public HTTPS link opens a dashboard that shows live transactions and fraud alerts.
2. Each alert states the reason in plain words, and cites the policy rule that it breaks.
3. Airflow runs the scheduled work. MLflow holds every model version. One version is live.
4. AWS Lambda scores a single transaction, and Terraform creates that Lambda.
5. GitHub Actions tests each push and deploys each merge to the server.
6. The platform runs for 3 months with no manual repair.

## Scope

| In | Out |
|---|---|
| Airflow, Delta Lake, MLflow, AWS Lambda, Terraform, GitHub Actions | Java, Greenplum, Jira |
| Kafka, Redis, Postgres, FastAPI, React | dbt, Spark, Flink, Airbyte, Feast |
| Claude API, pgvector, RAG, SHAP, Evidently | LangChain, Great Expectations, OpenMetadata |
| Prometheus, Grafana, Cloudflare Access | Kubernetes in production |

Kubernetes stays a local `kind` demonstration. It closes a gap on the card, but no reader sees it.

The stack in `AGENTS.md` changed on 2026-09-26. The reason is in
`brain/decisions/2026-09-26-the-demo-must-be-alive-not-runnable.md`.

## Capacity

The owner has under 5 hours each week, and the hours are irregular. There is no deadline.

This constraint shapes the plan more than any technical choice:

- Each session takes 2 to 4 hours.
- Each session ends with a commit and with code that runs.
- No session ends in the middle of a refactor.
- The last step of each session writes the first step of the next session.

## Stages

| Stage | Work | Hours | Result |
|---|---|---|---|
| A | Foundation and CI | 8 | A clean data model. Tests run on each push |
| B | Model and registry | 12 | A trained model. Every version recorded |
| C | Data quality | 5 | Bad data stops the DAG |
| D | Lambda and Terraform | 6 | AWS serves one score. Terraform owns it |
| E | Live backend and dashboard | 18 | Five tabs. Rows arrive on screen |
| F | Deploy and operate | 16 | A public link. Backups. Alerts to Telegram |
| G | AI layer | 12 | Policy citations. Plain-word reasons. An agent |

Total: 77 hours. At 4 hours each week, stage F completes in about 16 weeks.

Stage D closes 2 gaps on the card in 6 hours, and no reader sees it. Stage E takes 18 hours, and
every reader sees it. Both matter. Stage D comes first because it is cheap.

### Stage A: foundation and CI (8 hours)

1. Move `is_fraud` out of the transaction into `silver/labels`, with a `labelled_at` delay.
2. Remove the stray `__index_level_0__` column from silver and quarantine.
3. Raise `ACCOUNTS` from 2,000 to 40,000, to match the live rate.
4. Add the limitation note to `README.md`.
5. Add a GitHub Actions workflow that runs `ruff check` and `pytest`.

### Stage B: model and registry (12 hours)

1. Add `mlflow`, `scikit-learn`, `xgboost` and `shap`.
2. Write `finplat/train.py`. Split the data by time, never at random.
3. Report precision, recall and PR-AUC. Accuracy is useless at a 1.5% fraud rate.
4. Start the MLflow container. Log each run.
5. Promote one version to Production in the registry.
6. Add a weekly train task to the Airflow DAG.

### Stage C: data quality (5 hours)

Write `finplat/quality.py`. The project has 2 row rules today, and no other check.

| Check | Rule | Severity |
|---|---|---|
| Schema | The expected columns and types are present | Critical |
| Volume | Today is within 50% of the 7-day average | Critical |
| Freshness | Data arrived in the last 25 hours | Critical |
| Quarantine rate | Below 5% | Warning |
| Duplicate rate | Below 3% | Warning |
| Amount distribution | The median is within 3x of the 7-day median | Warning |

Each result writes to a `quality_checks` table. A critical failure stops the DAG.

### Stage D: Lambda and Terraform (6 hours)

1. Set an AWS budget alarm at USD 1. Do this before any other step.
2. Package the scorer as a zip Lambda. A zip avoids the ECR cost.
3. Deploy it by hand one time, to learn the shape.
4. Write the Terraform. Both `apply` and `destroy` must run clean.

### Stage E: live backend and dashboard (18 hours)

The generator writes straight to the API. Kafka is not needed yet, and the screen looks the same.

| Tab | Shows |
|---|---|
| Live | A start control, a rate control, a counter, and the rows as they arrive |
| Alerts | One card for each alert, with the reason and 2 decision buttons |
| Model | The live MLflow version, its metrics, and the drift report |
| Pipeline | The Airflow runs, the row counts, and the data quality grid |
| Ask | A question in plain words, answered from real rows |

The 2 decision buttons write to `silver/labels` with `label_source = analyst`. The loop closes.

### Stage F: deploy and operate (16 hours)

The server already exists. A decision of 2026-09-26 rents one HostHatch VPS with 16 GB for
`ai-trade` and for the other projects. This project is a tenant on that server. It does not rent
a second one. Hetzner lost on price in that decision.

1. Take a folder on the shared VPS, with its own `docker compose` file.
2. Set a memory limit on each container. The 16 GB is shared with the other projects.
3. Put the dashboard behind Cloudflare Access, the same login the other projects use.
4. Build the deploy pipeline. GitHub Actions builds the image. The server pulls it.
5. Take nightly Postgres backups, and copy them off the server. HostHatch support is slow.
6. Send an alert when the platform stops.
7. Add the retention jobs from the next section.

### Stage G: AI layer (12 hours)

1. Write a false internal fraud policy. About 12 rules.
2. Index it. Each alert cites the rule that it breaks.
3. An LLM turns the SHAP values and the policy rule into 2 sentences.
4. An investigation agent reads 30 days of history and writes a case note.
5. Store the prompts as files. Log the prompt version with each alert.
6. When the API fails, the card still shows the SHAP reasons.

## Configured limits

These are limits, not suggestions. The server holds other projects later.

| Setting | Value |
|---|---|
| Transaction rate | 1 each second, about 86,400 each day |
| Demo burst rate | 50 each second, for less than 60 seconds |
| Accounts | 40,000 |
| Bronze retention | 7 days |
| Silver and gold retention | 90 days |
| Delta VACUUM | Daily, retain 24 hours |
| Airflow log retention | 14 days |
| Kafka retention, if Kafka arrives | 6 hours |
| Lake size at steady state | About 530 MB |
| Total project disk budget | 8 GB on the shared VPS |

`build_gold` overwrites the whole table on each run. Without a daily VACUUM, the server holds one
full copy for each retained version.

## Optional menu

Pick 3 at most. Each item is real work, and the plan already holds 77 hours.

| Item | Hours | Value |
|---|---|---|
| An architecture diagram and a 90-second recording | 4 | The highest return in the project |
| An LLM evaluation set with a CI gate | 4 | Rare. The strongest AI engineer signal |
| Airflow depth: sensors, SLA, backfill, task mapping | 3 | The DAG reads as a tutorial today |
| Schema evolution and a contract test | 3 | A common data engineer question |
| Structured logs and secret hygiene | 4 | Needed before the platform grows |

## Future work

Not committed. Recorded so the idea survives.

- A live crypto trade feed from a public exchange websocket. The data is real, and it carries no
  labels. It teaches reconnection, gap detection, and out-of-order events. Add it only after
  stage F ends.
- Kafka, as a swap behind the existing API. About 12 hours.
- A local `kind` and Helm deployment. About 10 hours. No reader sees it.
- An incremental `build_gold`, to replace the full rebuild.

## Source

The build card "Regulated Finance Data Platform with MLflow, Airflow, and Kubernetes" in
resume-builder (build id `cmsoooga3000184v466azywjm`). After stage F, mark the card as built
with this repository.
