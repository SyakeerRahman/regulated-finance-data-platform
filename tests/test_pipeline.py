from datetime import date

import pandas as pd
import pytest
from deltalake import DeltaTable

from finplat.generate import generate
from finplat.pipeline import (
    BRONZE,
    GOLD,
    LABELS,
    QUARANTINE,
    SILVER,
    build_gold,
    load_bronze,
    load_labels,
    refine_silver,
    training_frame,
)

DAY = date(2026, 9, 1)
# Small enough that accounts build a history the features can compare against.
ACCOUNTS = 2_000


def run_day(lake: str, day: date, rows: int = 5_000) -> dict[str, int]:
    batch_id = day.isoformat()
    transactions, labels = generate(day, rows, seed=7, accounts=ACCOUNTS)
    load_bronze(lake, transactions, batch_id)
    load_labels(lake, labels, batch_id)
    counts = refine_silver(lake, batch_id)
    build_gold(lake)
    return counts


def rows(lake: str, table: str) -> int:
    return DeltaTable(f"{lake}/{table}").to_pyarrow_table().num_rows


def columns(lake: str, table: str) -> set[str]:
    return set(DeltaTable(f"{lake}/{table}").to_pandas().columns)


@pytest.fixture
def lake(tmp_path) -> str:
    return str(tmp_path / "lake")


def test_generate_is_repeatable():
    first_tx, first_labels = generate(DAY, 500, seed=7, accounts=ACCOUNTS)
    second_tx, second_labels = generate(DAY, 500, seed=7, accounts=ACCOUNTS)
    assert first_tx.equals(second_tx)
    assert first_labels.equals(second_labels)


def test_no_table_carries_the_answer_or_the_pandas_index(lake):
    run_day(lake, DAY)
    for table in (BRONZE, QUARANTINE, SILVER, GOLD):
        assert "is_fraud" not in columns(lake, table)
        assert "__index_level_0__" not in columns(lake, table)
    assert "is_fraud" in columns(lake, LABELS)


def test_a_label_arrives_after_its_transaction(lake):
    run_day(lake, DAY)
    labels = DeltaTable(f"{lake}/{LABELS}").to_pandas()
    day_start = pd.Timestamp(DAY, tz="UTC")
    assert (labels["labelled_at"] >= day_start + pd.Timedelta(days=30)).all()
    assert set(labels["label_source"]) == {"chargeback", "dispute window closed"}


def test_training_drops_transactions_nobody_has_judged_yet(lake):
    run_day(lake, DAY)
    early = training_frame(lake, pd.Timestamp(DAY, tz="UTC") + pd.Timedelta(days=40))
    late = training_frame(lake, pd.Timestamp(DAY, tz="UTC") + pd.Timedelta(days=120))
    assert 0 < len(early) < len(late)
    # Only fraud is confirmed inside the chargeback window, so the early set is almost all fraud.
    assert early["is_fraud"].mean() > late["is_fraud"].mean()


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
    tables = (BRONZE, QUARANTINE, SILVER, LABELS, GOLD)
    sizes = [rows(lake, t) for t in tables]
    assert run_day(lake, DAY) == first
    assert [rows(lake, t) for t in tables] == sizes


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
    known = training_frame(lake, pd.Timestamp("2027-01-01", tz="UTC"))
    known = known[known["ts"].dt.day == 3]
    fraud, legit = known[known["is_fraud"]], known[~known["is_fraud"]]
    assert fraud["amount_vs_account"].median() > 2 * legit["amount_vs_account"].median()
    assert fraud["is_abroad"].mean() > 3 * legit["is_abroad"].mean()
