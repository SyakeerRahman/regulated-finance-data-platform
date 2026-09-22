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
  AGENTS.md    # agent instructions (read first)
  README.md    # this file
  docs/        # the brief, the specs, the agent chain output
  data/        # local corpus and download scripts (payloads gitignored)
```

## Prerequisites

| Tool | Version | Used for |
| ---- | ------- | -------- |
| TODO | TODO    | TODO     |

## Running locally

To be added during the build.

## Local port

Not used yet.