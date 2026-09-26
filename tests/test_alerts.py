"""The alert store and the decision loop.

These need Postgres. They skip when nothing answers on the DSN, so a clone with no containers
running still gets a green suite.
"""

import pandas as pd
import psycopg
import pytest
from deltalake import DeltaTable

from finplat.alerts import CONFIRMED, FALSE_POSITIVE, OPEN, Store, export_decisions
from finplat.explain import Reason, sentence
from finplat.pipeline import LABELS
from finplat.settings import get_settings


def result(transaction_id: str, score: float = 0.97) -> dict:
    return {
        "transaction_id": transaction_id,
        "account_id": "ACC01518",
        "amount": 411.13,
        "country": "US",
        "channel": "online",
        "merchant_category": "fuel",
        "ts": pd.Timestamp("2026-09-26T11:58:39Z"),
        "score": score,
        "threshold": 0.9025,
        "model_version": "3",
    }


@pytest.fixture
def store() -> Store:
    store = Store(get_settings().postgres_dsn)
    try:
        store.migrate()
    except psycopg.OperationalError as error:
        pytest.skip(f"no Postgres on the DSN: {error}")
    with store.connect() as connection:
        connection.execute("truncate alerts restart identity")
    return store


def test_an_alert_is_stored_with_its_reason(store):
    alert_id = store.raise_alert(result("t-1"), "18x this account's normal spend.", {"amount_vs_account": 4.35})
    stored = store.recent()[0]

    assert stored["alert_id"] == alert_id
    assert stored["status"] == OPEN
    assert stored["reason"].startswith("18x")
    assert stored["contributions"] == {"amount_vs_account": 4.35}


def test_the_same_transaction_never_alerts_twice(store):
    """A restart replays the feed. An alert list that grows on every replay is unusable."""
    first = store.raise_alert(result("t-1"), "reason", {})
    second = store.raise_alert(result("t-1"), "reason", {})

    assert first is not None
    assert second is None
    assert len(store.recent()) == 1


def test_an_analyst_decision_changes_the_status(store):
    alert_id = store.raise_alert(result("t-1"), "reason", {})
    decided = store.decide(alert_id, CONFIRMED)

    assert decided["status"] == CONFIRMED
    assert decided["decided_at"] is not None
    assert store.counts() == {CONFIRMED: 1}


def test_an_invented_status_is_refused(store):
    alert_id = store.raise_alert(result("t-1"), "reason", {})
    with pytest.raises(ValueError, match="status must be one of"):
        store.decide(alert_id, "probably")


def test_a_decision_becomes_a_label(store, tmp_path):
    """The loop closing. What an analyst presses is what the next retrain learns from."""
    lake = str(tmp_path / "lake")
    store.decide(store.raise_alert(result("t-1"), "reason", {}), CONFIRMED)
    store.decide(store.raise_alert(result("t-2"), "reason", {}), FALSE_POSITIVE)

    assert export_decisions(store, lake) == 2

    labels = DeltaTable(f"{lake}/{LABELS}").to_pandas().set_index("transaction_id")
    assert set(labels["label_source"]) == {"analyst"}
    assert bool(labels.loc["t-1", "is_fraud"]) is True
    assert bool(labels.loc["t-2", "is_fraud"]) is False


def test_a_changed_mind_replaces_the_label(store, tmp_path):
    lake = str(tmp_path / "lake")
    alert_id = store.raise_alert(result("t-1"), "reason", {})
    store.decide(alert_id, CONFIRMED)
    export_decisions(store, lake)

    store.decide(alert_id, FALSE_POSITIVE)
    export_decisions(store, lake)

    labels = DeltaTable(f"{lake}/{LABELS}").to_pandas()
    assert len(labels) == 1
    assert bool(labels.iloc[0]["is_fraud"]) is False


def test_an_open_alert_is_not_a_label(store, tmp_path):
    store.raise_alert(result("t-1"), "reason", {})
    assert export_decisions(store, str(tmp_path / "lake")) == 0


def test_the_sentence_names_only_what_raised_the_score():
    reasons = [
        Reason("amount_vs_account", 4.36, "18x this account's normal spend"),
        Reason("is_abroad", 2.36, "a purchase outside Malaysia"),
        Reason("is_online", -1.72, "the card was present"),
    ]
    written = sentence(reasons)

    assert written == "18x this account's normal spend, and a purchase outside Malaysia."
    assert "card was present" not in written
    # str.capitalize would lowercase the rest and cost Malaysia its capital letter.
    assert "Malaysia" in written


def test_a_quiet_transaction_says_so():
    assert sentence([Reason("is_online", -1.0, "the card was present")]) == "Nothing in this transaction stands out."
