# Agent Instructions

This file is the source of truth for any coding agent in this repository. Read it before you
touch the code. The workspace defaults are in `brain/standards/coding-standards.md`. This file
states only the difference from those defaults.

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Orchestration | Apache Airflow, in Docker Compose for weekends 1 and 2 |
| Storage | Delta Lake tables through `deltalake` (delta-rs). No Spark |
| Model registry | MLflow tracking server |
| Model | scikit-learn |
| Serving | AWS Lambda, container image |
| Infrastructure | Terraform for AWS. kind and Helm for local Kubernetes |
| CI/CD | GitHub Actions |
| Data | Synthetic transactions from `data/generate.py`. No real customer data |

The stack is locked. Do not propose an alternative without a stated reason.

## Repo layout

```text
regulated-finance-data-platform/
  AGENTS.md    # this file
  README.md
  docs/        # the brief, the specs, the agent chain output
  data/        # local corpus and download scripts (payloads gitignored)
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