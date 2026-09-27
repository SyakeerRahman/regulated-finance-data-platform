"""Keep the lake and the logs inside the disk budget. The limits are in docs/brief.md.

Three jobs, all safe to run twice:

- `drop_expired` deletes whole batches past their table's limit.
- `vacuum_all` deletes the files that no table version still needs. `build_gold` overwrites the
  whole table each day, so without this the server keeps one full copy of gold per day.
- `delete_old_files` deletes Airflow task logs past their limit.

Trimming silver also trims history: an account's first transaction inside the window gets the
first-transaction feature value, although the account is older. That is the price of 90 days.
"""

import os
import time
from datetime import date, timedelta
from pathlib import Path

from deltalake import DeltaTable

from finplat.pipeline import BRONZE, GOLD, LABELS, QUARANTINE, SILVER
from finplat.quality import QUALITY

# Days each table keeps, counted back from the run date. The brief: bronze 7, silver and gold 90.
# Gold has no entry because it is rebuilt from silver, so trimming silver trims gold.
RETENTION_DAYS = {
    BRONZE: 7,
    QUARANTINE: 90,
    SILVER: 90,
    LABELS: 90,
    QUALITY: 90,
}
VACUUM_HOURS = 24
LOG_DAYS = 14

LIVE_PREFIX = "live-"


def batch_day(batch_id: str) -> date | None:
    """The day a batch holds, or None for a batch with no date.

    `2026-09-24` is a pipeline batch and `live-2026-09-24` is the live feed. The analyst labels sit
    in a batch called `analyst`, which has no date and is never expired: export rewrites it whole
    from Postgres each time.
    """
    try:
        return date.fromisoformat(batch_id.removeprefix(LIVE_PREFIX))
    except ValueError:
        return None


def expired_batches(batch_ids: list[str], today: date, days: int) -> list[str]:
    """The batches older than `days` before `today`. The batch on the cutoff day stays."""
    cutoff = today - timedelta(days=days)
    return sorted(b for b in batch_ids if (day := batch_day(b)) is not None and day < cutoff)


def drop_expired(lake: str, today: date) -> dict[str, int]:
    """Delete expired batches from every table. Returns the rows deleted from each."""
    deleted = {}
    for table, days in RETENTION_DAYS.items():
        path = f"{lake}/{table}"
        if not DeltaTable.is_deltatable(path):
            continue
        delta = DeltaTable(path)
        batch_ids = delta.to_pyarrow_table(columns=["batch_id"]).column("batch_id").unique().to_pylist()
        expired = expired_batches(batch_ids, today, days)
        if not expired:
            deleted[table] = 0
            continue
        # The ids come from the table and parsed as dates, so they hold no quote to escape.
        listed = ", ".join(f"'{b}'" for b in expired)
        metrics = delta.delete(predicate=f"batch_id IN ({listed})")
        deleted[table] = int(metrics.get("num_deleted_rows", 0))
    return deleted


def vacuum_all(lake: str, retention_hours: int = VACUUM_HOURS) -> dict[str, int]:
    """Remove files older than `retention_hours` that no current table version reads.

    delta-rs refuses less than 7 days by default. The brief sets 24 hours, because the only
    readers are this pipeline and the API, and neither reads a version a day old.
    """
    removed = {}
    for table in [*RETENTION_DAYS, GOLD]:
        path = f"{lake}/{table}"
        if not DeltaTable.is_deltatable(path):
            continue
        files = DeltaTable(path).vacuum(
            retention_hours=retention_hours, dry_run=False, enforce_retention_duration=False
        )
        removed[table] = len(files)
    return removed


def delete_old_files(root: str, days: int = LOG_DAYS, now: float | None = None) -> int:
    """Delete files under `root` last changed more than `days` ago, then any empty folders."""
    cutoff = (now if now is not None else time.time()) - days * 86_400
    removed = 0
    for folder, subfolders, files in os.walk(root, topdown=False):
        for name in files:
            path = Path(folder, name)
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        if folder != root and not os.listdir(folder):
            os.rmdir(folder)
    return removed
