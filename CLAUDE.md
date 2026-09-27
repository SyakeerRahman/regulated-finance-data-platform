# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read `AGENTS.md` first. It is the source of truth for the stack, the dependency policy, and the
code style. This file adds only the commands and the architecture notes.
The workspace rules are in `C:\Users\User\Project\CLAUDE.md`.

## Commands

| Task | Command |
|---|---|
| Install or sync the environment | `uv sync` |
| All tests | `uv run pytest` |
| One test file | `uv run pytest tests/test_pipeline.py` |
| One test | `uv run pytest tests/test_pipeline.py::test_rerunning_a_batch_changes_nothing` |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| One day of the pipeline, no Airflow | `uv run python -m finplat.run 2026-09-01` |
| Airflow | `docker compose up -d --build`, then http://localhost:8095 |
| Rebuild the Airflow image after a dependency change | `docker compose build` |

`.env` with `LAKE_URI=data/lake` must exist before any command that touches the lake. The settings
class has no default for it, so a missing value stops startup.

## Architecture

`finplat` holds all the logic. Everything else calls it.

- `generate.py` makes one day of synthetic transactions and injects broken rows on purpose
  (`DUPLICATE_RATE`, `MISSING_ACCOUNT_RATE`, `BAD_AMOUNT_RATE`). Those constants are what gives the
  silver layer work to do, and the tests assert that quarantine and duplicate counts are above zero.
  A change to them can break tests that never mention them.
- `pipeline.py` holds the three layer functions and the four table paths. Delta writes go through
  `deltalake` (delta-rs). There is no Spark and no Spark session to set up.
- `run.py` and `dags/transactions_to_delta.py` are two thin drivers over the same three functions.
  A DAG task must stay a call into `finplat`. Put logic in the package, not in the DAG.

Data flow: `generate` -> `load_bronze` -> `refine_silver` -> `build_gold`.

## The idempotency contract

Airflow retries tasks, so every step must leave the tables as one run would. Each layer keeps that
promise a different way, and a change to one must keep its own method:

- Bronze and quarantine: `_replace_batch` overwrites with the predicate `batch_id = '<id>'`, so a
  rerun replaces that partition and leaves other batches alone.
- Silver: a Delta `MERGE` on `transaction_id`, after `drop_duplicates` inside the batch.
- Gold: a full rebuild with `mode="overwrite"`, read from the whole silver table.

`test_rerunning_a_batch_changes_nothing` is the guard. Run it after any write-path change.

Gold reads all of silver, so it is not per-batch. That is why the DAG sets `max_active_runs=1`:
two concurrent runs would race on the silver merge and the gold rebuild.

## Feature rules

`build_gold` computes `amount_vs_account` from the account's mean of **earlier rows only**
(`cumsum - amount` over `cumcount`). A feature that sees the same row or later rows leaks the
future, scores well in training, and fails in production. `test_account_feature_uses_only_earlier_transactions`
rebuilds the value by hand to check this. First transaction of an account is `1.0`, not null.

## Determinism

`generate` seeds from `[seed, day.toordinal()]`, and account spend levels seed from `seed` alone.
The same day and seed always give the same batch. Tests depend on this, including
`test_fraud_is_learnable_from_the_features`, which asserts statistical separation and needs three
days of history to pass.

## Docker notes

The Airflow image copies `finplat` and `dags` in, for the server. The local compose file mounts
`./finplat` and `./dags` over those copies, so a local code edit needs no rebuild. A new
third-party dependency needs it in `requirements-airflow.txt` and a `docker compose build`.

`requirements-airflow.txt` pins the same versions as `pyproject.toml`, including pandas and
pyarrow, which replace the base image's own. Under the image's pandas 2 the bronze schema check
fails every batch. The build runs `pip check`, so a conflicting pin fails the build.

Both images run as uid 50000, group root. They share the lake volume, and a different uid would
stop one from appending to a table the other created.

## Project state

Stages A to E are done. Stage F (deploy) is in progress. The VPS is not rented yet and there is no
domain, so work that needs neither comes first.

Stage F so far: the repo is public on GitHub, CI runs the tests against a Postgres service, and each
green push to `main` publishes both images to GHCR, tagged with the commit SHA.
`deploy/compose.yml` is the server stack: Airflow, API, Postgres and MLflow, each with a memory
limit, and nothing on a public port. `deploy/bootstrap.sh` fills an empty server: 3 days of the
pipeline, one training run, promote version 1.

The whole stack was rehearsed on this PC on fresh volumes on 2026-09-27. The bootstrap took
1 min 40 s. Memory after one live feed and one daily run: Airflow 1.33 GiB, API 447 MiB,
MLflow 333 MiB, Postgres 44 MiB. Images unpacked: Airflow 2.98 GB, API 1.44 GB, MLflow 0.88 GB,
Postgres 0.31 GB. That is about 5.6 GB of the 8 GB project disk budget before any data.

Backups: `deploy/backup.sh` runs nightly from cron. It saves Postgres (`pg_dump`, then checked with
`pg_restore --list`) and MLflow (sqlite backup API plus the model files), and uploads them to any
S3-compatible bucket with `curl --aws-sigv4`, so no AWS CLI image is needed. The lake is not
saved, because it regenerates. `deploy/restore.sh <stamp>` reads the local copy, or fetches from
the bucket if there is none. After a lost server: `restore.sh`, then `bootstrap.sh`, which rebuilds
the lake and skips training when a model is already live. Rehearsed on 2026-09-27 against a local
MinIO (`quay.io/minio/minio`. The Docker Hub image is gone): total loss, then restore from the
bucket alone, then restore over changed data. All passed. The bucket is not chosen yet: AWS S3
or Cloudflare R2.

Retention: `finplat/retention.py`, run by the last two tasks of the daily DAG. Bronze keeps 7 days;
quarantine, silver, labels and quality keep 90. Gold follows silver. Every table is vacuumed
at 24 hours, and Airflow logs are deleted after 14 days. The analyst label partition has no
date and is never expired. Both tasks use `trigger_rule="all_done"`, so a failed quality gate
does not stop them. Rehearsed in Airflow on 2026-09-27: bronze trimmed, silver kept, and task
logs and DAG processor logs both land in the state volume. Not yet seen: a run where the
quality gate fails and retention still runs.

Stage F work left that needs nothing external: none. The rest needs the VPS, a domain and a
Cloudflare account: Cloudflare Access, deploy on merge, and an alert when the platform stops.
Choose the backup bucket (S3 or R2) at the same time.

The local MLflow stores model files at the plain path `/mlflow/artifacts`, which on Windows
resolved to `C:\mlflow` on the host. `deploy/compose.yml` serves artifacts over HTTP instead.

The plan changed on 2026-09-26 from a three-weekend batch demo to a live platform that stays
online. Read `docs/brief.md` for the seven stages, the hard disk limits, and the done criteria,
and `brain/decisions/2026-09-26-the-demo-must-be-alive-not-runnable.md` for why.

The label lives in `silver/labels` with a `labelled_at` delay, not on the transaction row. See
`brain/decisions/2026-09-26-the-label-is-not-a-column-on-the-transaction.md`. A lake written
before stage A still has `is_fraud` in bronze, and the live feed cannot append to it. Rebuild it:
move `data/lake` aside, then run `finplat.run` for 3 consecutive days.
