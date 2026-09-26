# Agent Instructions

This file is the source of truth for any coding agent in this repository. Read it before you
touch the code. The workspace defaults are in `brain/standards/coding-standards.md`. This file
states only the difference from those defaults.

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Orchestration | Apache Airflow, in Docker Compose |
| Storage | Delta Lake tables through `deltalake` (delta-rs). No Spark |
| Hot store | Redis for recent rows. Postgres for alerts, decisions and vectors |
| Model registry | MLflow tracking server |
| Model | XGBoost and scikit-learn. SHAP for each prediction. Evidently for drift |
| Serving | AWS Lambda, zip package. FastAPI on the server for the live path |
| Frontend | React, Vite, Tailwind, shadcn/ui, Recharts |
| AI | Claude API for narration and the agent. pgvector for retrieval |
| Infrastructure | Terraform for AWS. Docker Compose on the shared HostHatch VPS, behind Cloudflare Access. kind and Helm for local Kubernetes |
| Monitoring | Prometheus and Grafana |
| CI/CD | GitHub Actions. Tests on each push, deploy on each merge |
| Streaming | Kafka (Redpanda), deferred. The API takes the same interface until then |
| Data | Synthetic transactions from `finplat/generate.py`. No real customer data |

Out of scope: Java, Greenplum, Jira, dbt, Spark, Flink, Airbyte, Feast, Great Expectations,
LangChain, OpenMetadata, Kubernetes in production.

The stack is locked. Do not propose an alternative without a stated reason. It last changed on
2026-09-26. The reason is in `brain/decisions/2026-09-26-the-demo-must-be-alive-not-runnable.md`.
The plan and the hard limits are in `docs/brief.md`.

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

## Dependency policy

Default: write it yourself. Use a library only when the alternative is non-trivial, error-prone,
or a rewrite of a standard.

Answer 3 questions in the commit message before you add a runtime dependency:

1. What does it do that we cannot write in 30 lines of clear code?
2. How many callers use it?
3. What is its maintenance and transitive dependency cost?

## Configuration

One settings module is the source of truth for each service. App code never reads an environment
variable directly. Startup stops immediately when a required value is absent. No silent fallback.

## Code style

- Small, obvious functions. Clear names beat an abstraction.
- No premature abstraction. Extract at the third caller, not at the hypothetical one.
- Validate at the boundary only: HTTP input, external APIs, database writes, untrusted parsing.
- Comments explain why, never what.
- No em-dash and no en-dash. Use a plain hyphen.

## Writing

Every file that a person reads follows `brain/standards/technical-writing.md` (ASD-STE100).