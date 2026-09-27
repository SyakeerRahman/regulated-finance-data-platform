#!/usr/bin/env bash
# First start on an empty server. Run once, from this folder, after `docker compose up -d`.
#
# The API cannot start on an empty lake: it warms from silver and loads the production model.
# Until this script finishes, the api container restarts in a loop. That is expected.
#
# Steps: 3 days of the pipeline, one training run, promote version 1. Rehearsed on fresh volumes
# on 2026-09-27: 1 minute 40 seconds in total.
set -euo pipefail

# compose reads .env from this folder by default.
compose() { docker compose "$@"; }
in_airflow() { compose exec -T airflow "$@"; }

if in_airflow python -c "from deltalake import DeltaTable; import sys; sys.exit(0 if DeltaTable.is_deltatable('/lake/silver/transactions') else 1)"; then
    echo "The lake already has silver. This script is for an empty server only." >&2
    exit 1
fi

# The feature needs earlier rows for each account, and the model needs 3 days to learn from.
for days_ago in 3 2 1; do
    day=$(date -u -d "-${days_ago} days" +%F)
    echo "== pipeline ${day}"
    in_airflow airflow dags test transactions_to_delta "${day}"
done

# A cutoff in the future makes every generated label visible. Labels arrive with a delay, so a
# cutoff of today would hide most of them from the first model. The weekly DAG uses its run date.
echo "== train"
in_airflow airflow dags test train_fraud_model 2099-01-01

echo "== promote"
in_airflow python -m finplat.registry promote 1

echo "== waiting for the api"
for _ in $(seq 1 30); do
    if [ "$(docker inspect -f '{{.State.Health.Status}}' "$(compose ps -q api)")" = healthy ]; then
        echo "api is healthy"
        exit 0
    fi
    sleep 5
done
echo "api did not become healthy. Read: docker compose logs api" >&2
exit 1
