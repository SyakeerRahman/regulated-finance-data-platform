#!/usr/bin/env bash
# Nightly backup: Postgres and MLflow, copied off the server. Run from this folder by cron:
#
#     15 3 * * * cd /srv/finplat && ./backup.sh >> backups/backup.log 2>&1
#
# HostHatch support is slow, so a copy that lives only on the server is not a backup.
#
# What is saved and why:
#   postgres.dump   alerts and analyst decisions. A decision is a label and cannot be made again.
#   mlflow.tar.gz   the model registry and the model files. A retrain gives a different model.
# The lake is not saved. It is generated from a fixed seed, and bootstrap.sh rebuilds it.
set -euo pipefail

KEEP_LOCAL_DAYS=7

set -a
# shellcheck disable=SC1091
. ./.env
set +a
# shellcheck source=s3.sh
. ./s3.sh
require_backup_settings

stamp=$(date -u +%Y-%m-%dT%H%MZ)
dir="backups/${stamp}"
mkdir -p "${dir}"
echo "== ${stamp}"

# Custom format, so restore can drop and recreate each object and one bad table does not stop
# the rest.
docker compose exec -T postgres pg_dump --username finplat --format custom finplat > "${dir}/postgres.dump"
# A dump that pg_restore cannot list is not a backup. Check it now, not on the day it is needed.
docker compose exec -T postgres pg_restore --list < "${dir}/postgres.dump" > /dev/null

# sqlite's backup API copies a consistent database while MLflow is writing to it. A plain copy of
# the file can catch half a transaction.
docker compose exec -T mlflow python - > "${dir}/mlflow.tar.gz" <<'EOF'
import os, sqlite3, sys, tarfile

snapshot = "/tmp/mlflow-backup.db"
with sqlite3.connect("/mlflow/mlflow.db") as live, sqlite3.connect(snapshot) as copy:
    live.backup(copy)
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
    archive.add(snapshot, arcname="mlflow.db")
    # Absent until the first model is logged.
    if os.path.isdir("/mlflow/artifacts"):
        archive.add("/mlflow/artifacts", arcname="artifacts")
EOF
tar -tzf "${dir}/mlflow.tar.gz" mlflow.db > /dev/null

(cd "${dir}" && sha256sum postgres.dump mlflow.tar.gz > SHA256SUMS)

for file in postgres.dump mlflow.tar.gz SHA256SUMS; do
    s3_put "${dir}/${file}" "finplat/${stamp}/${file}"
done
echo "uploaded finplat/${stamp}: $(du -sh "${dir}" | cut -f1)"

# The bucket keeps the history. The server keeps a week, so a restore after a bad deploy needs no
# download.
find backups -mindepth 1 -maxdepth 1 -type d -mtime +"${KEEP_LOCAL_DAYS}" -exec rm -rf {} +
