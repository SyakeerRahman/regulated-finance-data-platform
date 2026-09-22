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
    generate.py             # synthetic transactions, dirty rows included
    pipeline.py             # bronze, silver (quarantine + merge), gold
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
4. Start Airflow: `docker compose up -d --build`
5. Open http://localhost:8095 and trigger `transactions_to_delta`.

## The tables

| Table | Written by | Holds |
|---|---|---|
| `bronze/transactions` | `load_bronze` | Each batch as received, partitioned by `batch_id` |
| `silver/quarantine` | `refine_silver` | Rejected rows, with the reason |
| `silver/transactions` | `refine_silver` | Valid rows, one per `transaction_id`, upserted with MERGE |
| `gold/transaction_features` | `build_gold` | Model features. Account history uses earlier rows only |

A rerun of a batch replaces that batch. It does not add a second copy.

## Local port

Airflow: 8095.
