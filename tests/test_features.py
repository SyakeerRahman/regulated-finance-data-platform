from datetime import date

import pytest

from finplat.features import FEATURE_COLUMNS, AccountHistory, frame_features, row_features
from finplat.generate import generate


@pytest.fixture
def batch():
    transactions, _ = generate(date(2026, 9, 1), 3_000, seed=7, accounts=200)
    clean = transactions[transactions["account_id"].notna() & (transactions["amount"] > 0)]
    return clean.drop_duplicates("transaction_id").sort_values(["ts", "transaction_id"], ignore_index=True)


def test_the_live_path_and_the_batch_path_agree(batch):
    """The rule this module exists for. A drift here is a model scoring different numbers in
    production than it scored in training, and nothing else in the system would notice."""
    expected = frame_features(batch)

    history = AccountHistory()
    for position, transaction in enumerate(batch.to_dict("records")):
        count, mean = history.prior(transaction["account_id"])
        live = row_features(transaction, count, mean)
        for column in FEATURE_COLUMNS:
            assert live[column] == pytest.approx(expected[column].iloc[position]), f"{column} differs at row {position}"
        history.add(transaction["account_id"], float(transaction["amount"]))


def test_the_first_transaction_of_an_account_is_not_unusual():
    transaction = {
        "ts": "2026-09-01T03:00:00Z",
        "amount": 500.0,
        "country": "US",
        "channel": "online",
        "merchant_category": "jewellery",
    }
    first = row_features(transaction, prior_count=0, prior_mean=None)
    assert first["amount_vs_account"] == 1.0
    assert first["prior_transactions"] == 0
    assert first["is_night"] and first["is_abroad"] and first["is_online"] and first["is_risky_category"]


def test_history_never_counts_the_current_transaction():
    history = AccountHistory()
    history.add("ACC00001", 10.0)
    history.add("ACC00001", 20.0)
    count, mean = history.prior("ACC00001")
    assert (count, mean) == (2, 15.0)
