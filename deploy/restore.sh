#!/usr/bin/env bash
# Put one backup back. Run from this folder, with the stack up:
#
#     ./restore.sh 2026-09-27T0315Z
#
# The backup is read from backups/<stamp>/ if it is there, and fetched from the bucket if not.
# That covers both cases: a bad deploy on a working server, and a new server after a lost one.
#
# This replaces the alerts, the decisions and the model registry with the backup's copy. Anything
# written after the backup was taken is lost.
set -euo pipefail

stamp="${1:?usage: ./restore.sh <stamp>, for example 2026-09-27T0315Z}"
dir="backups/${stamp}"

set -a
# shellcheck disable=SC1091
. ./.env
set +a
# shellcheck source=s3.sh
. ./s3.sh

if [ ! -f "${dir}/SHA256SUMS" ]; then
    require_backup_settings
    mkdir -p "${dir}"
    for file in postgres.dump mlflow.tar.gz SHA256SUMS; do
        s3_get "finplat/${stamp}/${file}" "${dir}/${file}"
    done
fi
(cd "${dir}" && sha256sum --check --quiet SHA256SUMS)

echo "== postgres"
# --clean --if-exists drops each object before it is recreated, so this works on an empty
# database and on one that already has tables.
docker compose exec -T postgres pg_restore --username finplat --dbname finplat \
    --clean --if-exists --no-owner --single-transaction < "${dir}/postgres.dump"

echo "== mlflow"
# MLflow must not hold the database open while it is replaced.
docker compose stop mlflow
docker compose run --rm --no-deps -T --entrypoint sh mlflow -c \
    'rm -rf /mlflow/mlflow.db /mlflow/artifacts && tar -xzf - -C /mlflow' < "${dir}/mlflow.tar.gz"
docker compose start mlflow

# The API loaded the old model at startup. A restart makes it read the restored registry.
docker compose restart api
echo "restored ${stamp}"
