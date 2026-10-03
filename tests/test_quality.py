from datetime import date

import pandas as pd
import pytest
from deltalake import DeltaTable

from finplat.generate import generate
from finplat.pipeline import BRONZE, LIVE_PREFIX, append_bronze, build_gold, load_bronze, load_labels, refine_silver
from finplat.quality import (
    CRITICAL,
    QUALITY,
    DataQualityError,
    history,
    record,
    run_checks,
)
from tests.test_pipeline import ACCOUNTS

DAYS = [date(2026, 9, day) for day in (1, 2, 3)]
# The clock the checks read. `ingested_at` is stamped with the real clock when a batch loads,
# so freshness is measured against now and not against the day the batch is named after.
NOW = pd.Timestamp.now(tz="UTC")


def load(lake: str, day: date, rows: int = 4_000) -> str:
    batch_id = day.isoformat()
    transactions, labels = generate(day, rows, seed=7, accounts=ACCOUNTS)
    load_bronze(lake, transactions, batch_id)
    load_labels(lake, labels, batch_id)
    refine_silver(lake, batch_id)
    build_gold(lake)
    return batch_id


@pytest.fixture
def lake(tmp_path) -> str:
    path = str(tmp_path / "lake")
    for day in DAYS:
        load(path, day)
    return path


def named(report, name):
    return next(check for check in report.checks if check.name == name)


def test_a_healthy_batch_passes_every_check(lake):
    report = run_checks(lake, DAYS[-1].isoformat(), as_of=NOW)
    assert report.failures == [], [f"{c.name}: {c.detail}" for c in report.failures]
    assert report.summary() == {"checks": 6, "failed": 0, "critical": 0}
    report.raise_on_critical()


def test_a_short_batch_stops_the_run(lake):
    """The failure a pipeline does not otherwise notice. The feed breaks, 400 rows arrive
    instead of 4,000, every task goes green and the dashboard quietly empties."""
    batch_id = load(lake, date(2026, 9, 4), rows=400)
    report = run_checks(lake, batch_id, as_of=NOW + pd.Timedelta(days=1))

    volume = named(report, "volume")
    assert not volume.passed and volume.severity == CRITICAL
    assert volume.detail.startswith("404 rows against")
    with pytest.raises(DataQualityError, match="volume"):
        report.raise_on_critical()


def test_the_live_feed_does_not_count_towards_the_daily_volume(lake):
    """Seen on 2026-10-03: a long live-feed day set the average, and a normal day failed."""
    live, _ = generate(DAYS[-1], 40_000, seed=11, accounts=ACCOUNTS)
    append_bronze(lake, live, LIVE_PREFIX + DAYS[-1].isoformat())
    batch_id = load(lake, date(2026, 9, 4))

    volume = named(run_checks(lake, batch_id, as_of=NOW), "volume")

    assert volume.passed, volume.detail
    # The three daily batches only. With the live partition counted, the average passes 15,000.
    assert "3-batch average" in volume.detail
    assert volume.threshold < 5_000


def test_a_stale_batch_stops_the_run(lake):
    report = run_checks(lake, DAYS[-1].isoformat(), as_of=NOW + pd.Timedelta(days=5))
    freshness = named(report, "freshness")
    assert not freshness.passed and freshness.severity == CRITICAL
    with pytest.raises(DataQualityError, match="freshness"):
        report.raise_on_critical()


def test_a_missing_column_stops_the_run(lake, tmp_path):
    """Drop a column straight into bronze, the way an upstream change would."""
    broken = str(tmp_path / "broken")
    batch_id = load(broken, DAYS[0])
    frame = DeltaTable(f"{broken}/{BRONZE}").to_pandas().drop(columns="currency")
    from deltalake import write_deltalake

    write_deltalake(f"{broken}/{BRONZE}", frame, mode="overwrite", schema_mode="overwrite")

    schema = named(run_checks(broken, batch_id, as_of=NOW), "schema")
    assert not schema.passed
    assert "currency is absent" in schema.detail


def test_a_batch_dated_in_the_future_stops_the_run(lake):
    """A producer with a wrong timezone writes tomorrow. That is not a fresh batch."""
    report = run_checks(lake, DAYS[-1].isoformat(), as_of=NOW - pd.Timedelta(days=2))
    freshness = named(report, "freshness")
    assert not freshness.passed
    assert "in the future" in freshness.detail


def test_a_warning_is_recorded_and_does_not_stop_the_run(lake):
    report = run_checks(lake, DAYS[-1].isoformat(), as_of=NOW)
    warnings = [check for check in report.checks if check.severity != CRITICAL]
    assert {check.name for check in warnings} == {"quarantine rate", "duplicate rate", "amount median"}
    # Nothing raises, whatever these say.
    report.raise_on_critical()


def test_the_results_are_stored_and_a_rerun_replaces_them(lake):
    batch_id = DAYS[-1].isoformat()
    report = run_checks(lake, batch_id, as_of=NOW)
    assert record(lake, report, as_of=NOW) == 6
    assert record(lake, report, as_of=NOW) == 6

    stored = DeltaTable(f"{lake}/{QUALITY}").to_pandas()
    assert len(stored[stored["batch_id"] == batch_id]) == 6
    assert set(stored["check"]) == {c.name for c in report.checks}


def test_the_history_reads_back_for_the_dashboard(lake):
    for day in DAYS:
        batch_id = day.isoformat()
        record(lake, run_checks(lake, batch_id, as_of=NOW), as_of=NOW)

    grid = history(lake)
    assert len(grid) == 6 * len(DAYS)
    assert set(grid["batch_id"]) == {day.isoformat() for day in DAYS}
