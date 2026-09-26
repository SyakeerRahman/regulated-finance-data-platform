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
    run.py                  # one day without Airflow
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

## The tables

| Table | Written by | Holds |
|---|---|---|
| `bronze/transactions` | `load_bronze` | Each batch as received, partitioned by `batch_id` |
| `silver/quarantine` | `refine_silver` | Rejected rows, with the reason |
| `silver/transactions` | `refine_silver` | Valid rows, one per `transaction_id`, upserted with MERGE |
| `silver/labels` | `load_labels` | The verdict on each transaction, and the date it was known |
| `gold/transaction_features` | `build_gold` | Model features. Account history uses earlier rows only |

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
2. The label delay is a simplification. A real chargeback window is often 120 days, and a
   customer call can arrive within hours.
3. No public dataset of labelled card transactions exists, because card scheme rules and privacy
   law forbid it. Synthetic data is the only option, not a shortcut.

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
