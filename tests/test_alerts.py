"""The alert store and the decision loop.

These need Postgres. They skip when nothing answers on the DSN, so a clone with no containers
running still gets a green suite.

They run in their own database, `finplat_test`, on the same server. The fixture truncates the
alerts table, and on the live database that would delete every real alert and every decision.
"""

import pandas as pd
import psycopg
import pytest
from deltalake import DeltaTable
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

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


TEST_DATABASE = "finplat_test"


@pytest.fixture(scope="session")
def test_dsn() -> str:
    """The live DSN pointed at a separate database, created on first use."""
    live = get_settings().postgres_dsn
    assert conninfo_to_dict(live).get("dbname") != TEST_DATABASE
    try:
        # CREATE DATABASE cannot run inside a transaction, so this connection autocommits.
        with psycopg.connect(live, autocommit=True) as connection:
            exists = connection.execute("select 1 from pg_database where datname = %s", (TEST_DATABASE,)).fetchone()
            if not exists:
                connection.execute(sql.SQL("create database {}").format(sql.Identifier(TEST_DATABASE)))
    except psycopg.OperationalError as error:
        pytest.skip(f"no Postgres on the DSN: {error}")
    return make_conninfo(live, dbname=TEST_DATABASE)


@pytest.fixture
def store(test_dsn) -> Store:
    store = Store(test_dsn)
    store.migrate()
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


def test_the_top_reason_is_the_largest_push_upwards(store):
    store.raise_alert(result("t-1"), "reason", {"amount": 0.4, "is_abroad": 2.1, "amount_vs_account": -3.0})

    rows, total = store.search()
    assert total == 1
    # The largest magnitude pulls the score down. The reason is what pushed it up.
    assert rows[0]["top_reason"] == "is_abroad"


def test_search_filters_combine_and_count_every_match(store):
    for index in range(5):
        store.raise_alert({**result(f"abroad-{index}"), "country": "SG"}, "reason", {"is_abroad": 2.0})
    for index in range(3):
        store.raise_alert({**result(f"home-{index}"), "country": "MY"}, "reason", {"amount": 1.0})
    store.decide(store.search(text="home-0")[0][0]["alert_id"], CONFIRMED)

    page, total = store.search(country="SG", reason="is_abroad", limit=2)
    assert total == 5
    assert len(page) == 2

    assert store.search(country="MY", status=OPEN)[1] == 2
    assert store.search(text="HOME-")[1] == 3


def test_a_bulk_decision_changes_only_the_selected_alerts(store):
    ids = [store.raise_alert(result(f"t-{index}"), "reason", {}) for index in range(4)]

    assert store.decide_many(ids[:3], FALSE_POSITIVE) == 3
    assert store.counts() == {FALSE_POSITIVE: 3, OPEN: 1}
    with pytest.raises(ValueError):
        store.decide_many(ids, "probably")


def test_the_summary_compares_the_last_day_with_the_day_before(store):
    store.raise_alert(result("today"), "reason", {"amount": 1.0})
    store.raise_alert(result("yesterday"), "reason", {"is_abroad": 1.0})
    with store.connect() as connection:
        connection.execute(
            "update alerts set created_at = now() - interval '30 hours' where transaction_id = 'yesterday'"
        )

    now = pd.Timestamp.now(tz="UTC").to_pydatetime()
    summary = store.summary(now)
    assert summary["current"] == {OPEN: 1}
    assert summary["reasons"] == [["amount", 1]]
    # The store is only 30 hours old, so there is no whole day before this one to compare with.
    assert summary["previous"] is None

    with store.connect() as connection:
        connection.execute(
            "update alerts set created_at = now() - interval '47 hours' where transaction_id = 'yesterday'"
        )
        connection.execute(
            "insert into alerts (transaction_id, account_id, amount, country, channel, category, occurred_at, score, threshold, reason, contributions, created_at) values ('old', 'A', 1, 'MY', 'chip', 'fuel', now(), 0.95, 0.9, 'r', '{}', now() - interval '50 hours')"
        )
    summary = store.summary(now)
    assert summary["previous"] == {OPEN: 1}
    assert sum(row["n"] for row in summary["hourly"]) == 1


def test_reason_weights_average_each_feature_over_the_alerts_it_appears_in(store):
    store.raise_alert(result("t-1"), "reason", {"amount": 3.0, "is_abroad": 1.0})
    store.raise_alert(result("t-2"), "reason", {"amount": 1.0})

    assert store.reason_weights() == [["amount", 2.0, 2], ["is_abroad", 1.0, 1]]


def test_the_most_similar_alert_shares_the_pattern(store):
    """Abroad, online, resellable and large: the twin comes first, the corner-shop payment last."""
    pattern = {"country": "SG", "channel": "online", "merchant_category": "electronics"}
    target = store.raise_alert(
        {**result("target"), **pattern, "amount": 900.0, "score": 0.9995}, "r", {"is_abroad": 2.0}
    )
    twin = store.raise_alert({**result("twin"), **pattern, "amount": 850.0, "score": 0.9990}, "r", {"is_abroad": 1.5})
    cousin = store.raise_alert(
        {**result("cousin"), **pattern, "country": "MY", "amount": 300.0, "score": 0.97}, "r", {"amount": 1.0}
    )
    stranger = store.raise_alert(
        {
            **result("stranger"),
            "country": "MY",
            "channel": "chip",
            "merchant_category": "grocery",
            "amount": 12.0,
            "score": 0.91,
        },
        "r",
        {"amount_vs_account": 1.0},
    )

    similar = store.similar(target, limit=3)

    assert [row["alert_id"] for row in similar] == [twin, cousin, stranger]
    assert similar[0]["distance"] < similar[1]["distance"] < similar[2]["distance"]
    assert target not in [row["alert_id"] for row in similar]
