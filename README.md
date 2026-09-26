# regulated-finance-data-platform

A fraud-scoring data platform for a regulated finance setting. Airflow loads transactions into
Delta Lake tables. MLflow trains and registers a fraud model. An AWS Lambda function scores
transactions with the model. The stack runs on Kubernetes, and GitHub Actions tests each push.
The data is synthetic. The brief is in [docs/brief.md](docs/brief.md).

## Stack

| Layer | Choice |
| ----- | ------ |
| Orchestration | Apache Airflow |
| Storage | Delta Lake (delta-rs) |
| Model registry | MLflow |
| Serving | AWS Lambda |
| Infrastructure | Terraform, Kubernetes (kind, Helm) |
| CI/CD | GitHub Actions |

## Repo layout

```text
regulated-finance-data-platform/
  AGENTS.md                 # agent instructions (read first)
  README.md
  finplat/                  # the pipeline package
    settings.py             # the one settings module. Reads LAKE_URI
    generate.py             # synthetic transactions and their late labels
    pipeline.py             # bronze, silver (quarantine + merge + labels), gold
    train.py                # fit a model on the labels known by a cutoff date
    registry.py             # list the model versions, promote one to production
    features.py             # the one definition of a feature, batch and live
    domain.py               # the vocabulary. Imports nothing, so the Lambda can carry it
    export.py               # the live model as plain JSON, for the Lambda
    quality.py              # the checks that stop a bad batch reaching gold
    explain.py              # SHAP, turned into a sentence an analyst reads
    alerts.py               # alerts and analyst decisions, in Postgres
    run.py                  # one day without Airflow
  api/                      # the live path: generate, score, stream to the browser
  web/                      # the dashboard. React, Vite, Tailwind. Builds into api/static
  lambda_fn/                # AWS Lambda: score one transaction, standard library only
  terraform/                # the Lambda, its role, its log group, its URL
  scripts/                  # build the Lambda zip
  dags/                     # Airflow DAGs. Thin: each task calls finplat
  tests/                    # pytest, against a temporary lake
  docs/                     # the brief
  data/lake/                # Delta tables (gitignored)
  Dockerfile                # Airflow image plus the pipeline libraries
  docker-compose.yml        # Airflow standalone on port 8095
```

## Prerequisites

| Tool | Version | Used for |
| ---- | ------- | -------- |
| uv | 0.12 or later | Python environment and tests |
| Docker Desktop | 29 or later | Airflow |

## Running locally

1. Make the settings file: `echo LAKE_URI=data/lake > .env`
2. Run the tests: `uv run pytest`
3. Run one day without Airflow: `uv run python -m finplat.run 2026-09-01`
4. Start Airflow and MLflow: `docker compose up -d --build`
5. Open http://localhost:8095 and trigger `transactions_to_delta`.
6. Train a model: `uv run python -m finplat.train 2027-01-01`
7. Promote it: `uv run python -m finplat.registry list`, then `... promote <version>`
8. Open http://localhost:8096 to compare the runs.
9. Build the dashboard: `cd web && npm install && npm run build`
10. Start the service: `uv run uvicorn api.main:app --port 8097`
11. Open http://localhost:8097 and press Start.

`cd web && npm run dev` serves the dashboard on port 5173 with live reload and forwards `/api`
to uvicorn. `npm run build` writes into `api/static`, which is gitignored, so one service
answers in production.

## The tables

| Table | Written by | Holds |
|---|---|---|
| `bronze/transactions` | `load_bronze` | Each batch as received, partitioned by `batch_id` |
| `silver/quarantine` | `refine_silver` | Rejected rows, with the reason |
| `silver/transactions` | `refine_silver` | Valid rows, one per `transaction_id`, upserted with MERGE |
| `silver/labels` | `load_labels` | The verdict on each transaction, and the date it was known |
| `gold/transaction_features` | `build_gold` | Model features. Account history uses earlier rows only |
| `quality/checks` | `record` | One row per check per batch, with the value and the threshold |

A rerun of a batch replaces that batch. It does not add a second copy.

## The label is not a column on the transaction

No table of transactions holds `is_fraud`. A bank does not know the answer when a payment
arrives. The answer comes 30 to 90 days later, from a chargeback, a customer call, or an analyst.

`silver/labels` holds the answer with a `labelled_at` date. `training_frame(lake, as_of)` joins
the two and drops every transaction that nobody had judged on that date. A model trained today
therefore cannot read a verdict that arrives next month.

## What this data is not

The data is synthetic. `finplat/generate.py` decides fraud first, then draws the amount, the
hour, the country, the category and the channel from different distributions. The generator does
not detect fraud. It declares fraud, and then it writes the evidence.

Three limits follow, and they apply to every result in this repository:

1. The model scores well because the pattern is clean and deliberate. Real fraud overlaps with
   real spending, and a real fraudster changes tactics as soon as a rule catches them.
   An earlier version was worse: it drew every honest hour from 6 to 23, so nothing legitimate
   happened at 3am and `is_night` was the answer rather than a signal. Honest hours now follow a
   daily curve, and night holds about 3.5% of legitimate volume and 20.9% fraud.
2. The label delay is a simplification. A real chargeback window is often 120 days, and a
   customer call can arrive within hours.
3. No public dataset of labelled card transactions exists, because card scheme rules and privacy
   law forbid it. Synthetic data is the only option, not a shortcut.

## Data quality

A pipeline rarely fails loudly. It usually succeeds and produces rubbish. So the checks that
matter are not the row rules in `refine_silver`. They are the ones that compare today against
the days before it.

| Check | Rule | Severity |
| ----- | ---- | -------- |
| schema | Every column is present with the expected type | Critical |
| freshness | The newest row landed in the last 25 hours, and is not dated ahead | Critical |
| volume | Today is within 50% of the average of the last 7 batches | Critical |
| quarantine rate | Below 5% | Warning |
| duplicate rate | Below 3% | Warning |
| amount median | Within 3x of the median of the last 7 batches | Warning |

A critical failure stops the DAG between silver and gold. Gold then keeps yesterday, which is
correct data, instead of being rebuilt from a feed that broke overnight.

Every check reads the tables. None takes a count from `refine_silver`, because a check that
trusts the process it checks is not a check.

Measured: a batch of 808 rows against a 3-batch average of 20,200 stops the run, and gold stays
at 59,523 rows.

## The model

`finplat/train.py` fits an XGBoost classifier on the gold features. Two rules hold it honest:

1. **The split is by time.** The oldest 80% of judged rows train the model. The newest 20% test
   it. A random split lets the model learn from Thursday to predict Wednesday.
2. **The cutoff is a label date.** `training_frame(lake, as_of)` drops every transaction whose
   verdict arrived after `as_of`, so a run cannot read a chargeback that has not happened.

Accuracy is not reported. At a 1.5% fraud rate, a model that always answers "not fraud" scores
98.5%. The run reports PR-AUC, and the precision and recall at the best threshold.

MLflow records each run and registers the model. One version carries the alias `production`:

```text
version pr_auc    precision   recall    alias
2       0.9478    0.9665      0.8587    production
1       0.9478    0.9665      0.8587    -
```

The scorer loads `models:/fraud_model@production`, never a file path. A promotion therefore
changes the live model with no deploy, and a rollback is one command.

Read the PR-AUC of 0.95 against the limits in "What this data is not". The generator writes a
clean fraud pattern on purpose, so a high score measures the data and not the model.

## Local ports

| Port | Service |
| ---- | ------- |
| 8095 | Airflow |
| 8096 | MLflow |
| 8097 | The live page |
| 5440 | Postgres |

## The live path

`api/main.py` invents a transaction, scores it, and pushes it to the browser over server-sent
events. Three rules hold it to the batch path:

1. **One feature definition.** `finplat/features.py` holds the arithmetic. `build_gold` calls
   `frame_features` for a whole table and the scorer calls `row_features` for one transaction.
   A test replays 3,000 rows through both and asserts every column agrees.
2. **The history is warmed from the lake.** A scorer that starts cold treats every account as
   new, so `amount_vs_account` is 1.0 on every row and the model scores a distribution it never
   trained on.
3. **The threshold comes from the model.** Training picks it from the precision-recall curve and
   logs it. The service reads it from the registry. A number typed into the service drifts away
   from the model at the first retrain.

Measured at 50 transactions each second: 0.93% of rows raise an alert, against a 1.5% fraud
rate.

## The dashboard

Five tabs, at http://localhost:8097.

| Tab | Shows |
| --- | ----- |
| Live | Start and rate controls, a counter, a volume chart, and the rows as they arrive |
| Alerts | One card for each alert, its reason, and the two decision buttons |
| Model | The live MLflow version, every registered version, and why accuracy is not reported |
| Pipeline | The data quality grid, one column for each batch |
| Account | One account's history, its features, and the alerts raised against it |

Three rules the charts follow:

1. **One y-axis.** Alerts are a subset of transactions, so they share the scale. A second axis
   would draw a 1% rate at the same height as a 50% one.
2. **A legend is always present for two series.** Identity never rests on colour alone.
3. **The quality grid carries a glyph, not only a colour.** Status good and status critical
   measure 4.1 apart under deuteranopia, so the tick and the cross carry the meaning.

The palette is the validated dark instance: series blue `#3987e5` and orange `#d95926` on
surface `#1a1a19`, which measure CVD Delta E 26.8 and normal-vision 31.8 apart.

## Alerts, and the loop that closes

An alert is a row in Postgres, not in Delta and not in Redis. It has to survive a restart, be
fetched by its own id, and change state when somebody presses a button.

Each alert carries its reason. SHAP reports how much each feature moved the score, and
`finplat/explain.py` turns the top three into a sentence:

```text
#8  ACC39002  MYR 153.14  SG/online/electronics
  score 1.000 (threshold 0.902), model v3, status open
  WHY: 18x this account's normal spend, a purchase outside Malaysia, and a category that
       resells easily.
  contributions: {'amount_vs_account': 4.36, 'is_abroad': 2.36, 'is_risky_category': 1.56}
```

SHAP walks every tree, so it runs for an alert and not for the 99% of rows that pass.

An analyst then confirms the alert or calls it a false positive. That decision is the same kind
of fact as a chargeback, so it goes into `silver/labels` with `label_source = analyst` and the
next retrain reads it. Measured end to end: two decisions, and a retrain run today sees exactly
those two rows, because every other label is still inside its dispute window.

## The live feed writes to the lake in batches

Each Delta write makes a parquet file and a log entry. One write per transaction would leave
86,400 files a day and a table nothing can open. So the scorer buffers and flushes every 2,000
rows or 60 seconds, whichever comes first.

The live feed owns its own partition, `live-<date>`. The daily DAG owns the partition named
after the date, and two writers on one partition is how a replace deletes the other writer's
rows.

## The Lambda scores without XGBoost

A zip Lambda has 50 MB zipped and 250 MB unzipped. XGBoost and its shared library spend most of
that on their own, and importing them costs seconds on a cold start.

A gradient boosted tree is only a list of if-then branches. So `finplat/export.py` writes the
300 trees as JSON, and `lambda_fn/scorer.py` walks them in about 60 lines of standard library.

| | Value |
| --- | ----- |
| Package | 115 KB |
| Third-party dependencies | none |
| Cold import | 64 ms |
| One score | 0.9 ms |

Two implementations of one model is the shape of a silent production bug, so
`tests/test_lambda.py` scores the same 295 rows through the Lambda scorer and through XGBoost,
and asserts they agree to 1e-6.

The Lambda holds no state. The caller sends the account history, because reading the lake on
every request would cost more than the score is worth.

Deployment is in [terraform/README.md](terraform/README.md). It creates one Lambda, one role,
one log group and one Function URL. There is no API Gateway, no ECR and no S3 bucket, so nothing
starts to charge after 12 months.
