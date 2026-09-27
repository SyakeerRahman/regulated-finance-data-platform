import os
import time
from datetime import date, timedelta

import pytest
from deltalake import DeltaTable

from finplat.generate import generate
from finplat.pipeline import (
    BRONZE,
    GOLD,
    LABELS,
    SILVER,
    append_bronze,
    build_gold,
    load_bronze,
    load_labels,
    refine_silver,
    replace_batch,
)
from finplat.retention import batch_day, delete_old_files, drop_expired, expired_batches, vacuum_all

FIRST = date(2026, 9, 1)


def batches(lake: str, table: str) -> set[str]:
    return set(DeltaTable(f"{lake}/{table}").to_pandas(columns=["batch_id"])["batch_id"])


@pytest.fixture
def lake(tmp_path) -> str:
    """Three pipeline days, one live batch, and the analyst labels."""
    lake = str(tmp_path / "lake")
    for offset in range(3):
        day = FIRST + timedelta(days=offset)
        transactions, labels = generate(day, 500, seed=7, accounts=200)
        load_bronze(lake, transactions, day.isoformat())
        load_labels(lake, labels, day.isoformat())
        refine_silver(lake, day.isoformat())
    build_gold(lake)
    append_bronze(lake, transactions.head(10), "live-2026-09-01")
    analyst = labels.head(3).assign(label_source="analyst")
    replace_batch(f"{lake}/{LABELS}", analyst.assign(batch_id="analyst"), "analyst")
    return lake


def test_a_batch_id_names_its_day_or_none():
    assert batch_day("2026-09-24") == date(2026, 9, 24)
    assert batch_day("live-2026-09-24") == date(2026, 9, 24)
    assert batch_day("analyst") is None


def test_the_batch_on_the_cutoff_day_is_kept():
    ids = ["2026-09-01", "2026-09-02", "live-2026-09-01", "analyst"]
    assert expired_batches(ids, today=date(2026, 9, 9), days=7) == ["2026-09-01", "live-2026-09-01"]


def test_bronze_keeps_a_week_and_silver_keeps_the_rest(lake):
    deleted = drop_expired(lake, today=FIRST + timedelta(days=9))

    # The cutoff is 2026-09-03. Bronze loses the first two days and the live batch of day one.
    assert batches(lake, BRONZE) == {"2026-09-03"}
    assert deleted[BRONZE] > 0
    assert batches(lake, SILVER) == {"2026-09-01", "2026-09-02", "2026-09-03"}
    assert deleted[SILVER] == 0


def test_silver_and_labels_lose_a_day_after_ninety(lake):
    drop_expired(lake, today=FIRST + timedelta(days=91))

    assert batches(lake, SILVER) == {"2026-09-02", "2026-09-03"}
    # The analyst decisions have no date. They stay until an export replaces them.
    assert batches(lake, LABELS) == {"2026-09-02", "2026-09-03", "analyst"}


def test_running_retention_twice_deletes_nothing_the_second_time(lake):
    today = FIRST + timedelta(days=91)
    drop_expired(lake, today)
    assert all(rows == 0 for rows in drop_expired(lake, today).values())


def test_vacuum_removes_the_files_of_old_gold_versions(lake):
    build_gold(lake)
    rows = len(DeltaTable(f"{lake}/{GOLD}").to_pandas())

    removed = vacuum_all(lake, retention_hours=0)

    assert removed[GOLD] > 0
    # The current version still reads in full.
    assert len(DeltaTable(f"{lake}/{GOLD}").to_pandas()) == rows


def test_old_log_files_go_and_recent_ones_stay(tmp_path):
    old = tmp_path / "dag_id=x" / "run_id=old" / "attempt=1.log"
    new = tmp_path / "dag_id=x" / "run_id=new" / "attempt=1.log"
    for path in (old, new):
        path.parent.mkdir(parents=True)
        path.write_text("log")
    now = time.time()
    fifteen_days_ago = now - 15 * 86_400
    os.utime(old, (fifteen_days_ago, fifteen_days_ago))

    assert delete_old_files(str(tmp_path), days=14, now=now) == 1
    assert new.exists()
    assert not old.parent.exists()


def test_a_table_that_does_not_exist_yet_is_skipped(tmp_path):
    assert drop_expired(str(tmp_path / "empty"), today=FIRST) == {}
    assert vacuum_all(str(tmp_path / "empty")) == {}
