from datetime import date

import pytest
from deltalake import DeltaTable

from finplat.generate import generate
from finplat.pipeline import BRONZE, GOLD, QUARANTINE, SILVER, build_gold, load_bronze, refine_silver

DAY = date(2026, 9, 1)


def run_day(lake: str, day: date, rows: int = 5_000) -> dict[str, int]:
    batch_id = day.isoformat()
    load_bronze(lake, generate(day, rows, seed=7), batch_id)
    counts = refine_silver(lake, batch_id)
    build_gold(lake)
    return counts


def rows(lake: str, table: str) -> int:
    return DeltaTable(f"{lake}/{table}").to_pyarrow_table().num_rows


@pytest.fixture
def lake(tmp_path) -> str:
    return str(tmp_path / "lake")


def test_generate_is_repeatable():
    assert generate(DAY, 500, seed=7).equals(generate(DAY, 500, seed=7))


def test_every_received_row_is_accounted_for(lake):
    counts = run_day(lake, DAY)
    assert counts["received"] == counts["quarantined"] + counts["duplicates"] + counts["accepted"]
    assert counts["quarantined"] > 0 and counts["duplicates"] > 0


def test_quarantine_holds_only_bad_rows_with_a_reason(lake):
    run_day(lake, DAY)
    bad = DeltaTable(f"{lake}/{QUARANTINE}").to_pandas()
    assert set(bad["reason"]) <= {"missing account", "non-positive amount"}
    assert (bad["account_id"].isna() | (bad["amount"] <= 0)).all()


def test_silver_has_no_duplicate_or_bad_rows(lake):
    run_day(lake, DAY)
    silver = DeltaTable(f"{lake}/{SILVER}").to_pandas()
    assert silver["transaction_id"].is_unique
    assert silver["account_id"].notna().all()
    assert (silver["amount"] > 0).all()


def test_rerunning_a_batch_changes_nothing(lake):
    first = run_day(lake, DAY)
    sizes = [rows(lake, t) for t in (BRONZE, QUARANTINE, SILVER, GOLD)]
    assert run_day(lake, DAY) == first
    assert [rows(lake, t) for t in (BRONZE, QUARANTINE, SILVER, GOLD)] == sizes


def test_a_second_day_adds_to_the_tables(lake):
    one = run_day(lake, DAY)["accepted"]
    two = run_day(lake, date(2026, 9, 2))["accepted"]
    assert rows(lake, SILVER) == one + two
    assert rows(lake, GOLD) == one + two


def test_account_feature_uses_only_earlier_transactions(lake):
    run_day(lake, DAY)
    gold = DeltaTable(f"{lake}/{GOLD}").to_pandas().sort_values(["ts", "transaction_id"])
    first = gold.groupby("account_id").head(1)
    assert (first["prior_transactions"] == 0).all()
    assert (first["amount_vs_account"] == 1.0).all()

    # Rebuild the feature by hand for one account, one transaction at a time.
    account = gold["account_id"].value_counts().index[0]
    history = gold[gold["account_id"] == account]
    amounts = history["amount"].tolist()
    expected = [1.0] + [amounts[i] / (sum(amounts[:i]) / i) for i in range(1, len(amounts))]
    assert history["amount_vs_account"].tolist() == pytest.approx(expected)


def test_fraud_is_learnable_from_the_features(lake):
    # Night fraud comes before the day's other spending, so it needs earlier days as history.
    for day in (1, 2, 3):
        run_day(lake, date(2026, 9, day), rows=20_000)
    gold = DeltaTable(f"{lake}/{GOLD}").to_pandas()
    gold = gold[gold["ts"].dt.day == 3]
    fraud, legit = gold[gold["is_fraud"]], gold[~gold["is_fraud"]]
    assert fraud["amount_vs_account"].median() > 2 * legit["amount_vs_account"].median()
    assert fraud["is_abroad"].mean() > 3 * legit["is_abroad"].mean()
