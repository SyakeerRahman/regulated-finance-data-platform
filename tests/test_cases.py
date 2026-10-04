"""Past cases as vectors: what is indexed, which outcome each case carries, and what a search may see.

They need Postgres with pgvector, and run in the test database like the alert tests. The embedder
is fake: it turns a text into a short vector by its words, so a search result is predictable.
"""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from deltalake import write_deltalake

from finplat import ai_eval, cases
from finplat.alerts import CONFIRMED, FALSE_POSITIVE, OPEN, export_decisions
from finplat.embed import Embedder
from finplat.pipeline import LABELS
from tests.test_alerts import result, store, test_dsn  # noqa: F401 - pytest finds fixtures by name

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class FakeEmbedder(Embedder):
    """Electronics, groceries, and everything else, as three axes. Counts every request."""

    def __init__(self) -> None:
        super().__init__("http://fake", "fake-embed", 3, "key")
        self.requests: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.requests.append(list(texts))
        return [[float("electronics" in text), float("grocery" in text), 0.1] for text in texts]


def summarised(store, transaction_id: str, category: str, created_at: datetime) -> int:  # noqa: F811
    """One alert with an AI summary, raised at `created_at`."""
    alert_id = store.raise_alert({**result(transaction_id), "merchant_category": category}, "reason", {})
    store.save_narrative(
        alert_id,
        {"summary": f"A {category} payment abroad.", "suggestion": "unsure", "confidence": "low", "check": "x"},
        "fake-model",
        "v1",
    )
    with store.connect() as connection:
        connection.execute("update alerts set created_at = %s where alert_id = %s", (created_at, alert_id))
    return alert_id


def set_outcome(store, alert_id: int, outcome: str, source: str, known_at: datetime) -> None:  # noqa: F811
    with store.connect() as connection:
        connection.execute(
            "update case_vectors set outcome = %s, outcome_source = %s, known_at = %s where alert_id = %s",
            (outcome, source, known_at, alert_id),
        )


def found(store, as_of: datetime, query: str = "electronics") -> dict[int, dict]:  # noqa: F811
    vector = FakeEmbedder().embed([query])[0]
    return {row["alert_id"]: row for row in cases.search(store, vector, "fake-embed", as_of)}


@pytest.fixture
def lake(tmp_path) -> str:
    return str(tmp_path / "lake")


def test_a_search_shows_only_what_was_known_when_the_alert_was_raised(store, lake):  # noqa: F811
    """The same leak rule as amount_vs_account: a past case may not carry a verdict from the future."""
    known_before = summarised(store, "t-1", "electronics", T0 - timedelta(days=3))
    known_after = summarised(store, "t-2", "electronics", T0 - timedelta(days=2))
    raised_after = summarised(store, "t-3", "electronics", T0 + timedelta(hours=1))
    cases.index(store, FakeEmbedder(), lake)
    set_outcome(store, known_before, CONFIRMED, cases.ANALYST, T0 - timedelta(days=1))
    set_outcome(store, known_after, CONFIRMED, cases.LABEL, T0 + timedelta(days=40))

    seen = found(store, as_of=T0)

    assert seen[known_before]["outcome"] == CONFIRMED
    assert seen[known_before]["outcome_source"] == cases.ANALYST
    # The case existed. Its verdict did not, yet.
    assert seen[known_after]["outcome"] is None
    assert seen[known_after]["outcome_source"] is None
    # A case raised after the alert is not a past case.
    assert raised_after not in seen

    # Forty-one days on, the label is known.
    assert found(store, as_of=T0 + timedelta(days=41))[known_after]["outcome"] == CONFIRMED


def test_the_nearest_case_comes_first(store, lake):  # noqa: F811
    groceries = summarised(store, "t-1", "grocery", T0 - timedelta(days=2))
    electronics = summarised(store, "t-2", "electronics", T0 - timedelta(days=1))
    cases.index(store, FakeEmbedder(), lake)

    ranked = cases.search(store, FakeEmbedder().embed(["electronics"])[0], "fake-embed", T0)

    assert [row["alert_id"] for row in ranked] == [electronics, groceries]
    assert ranked[0]["distance"] < ranked[1]["distance"]


def test_a_second_index_run_sends_nothing_and_changes_nothing(store, lake):  # noqa: F811
    summarised(store, "t-1", "electronics", T0)
    summarised(store, "t-2", "grocery", T0)
    store.raise_alert(result("t-3"), "no summary yet", {})
    embedder = FakeEmbedder()

    first = cases.index(store, embedder, lake)
    with store.connect() as connection:
        before = connection.execute("select * from case_vectors order by alert_id").fetchall()
    second = cases.index(store, embedder, lake)
    with store.connect() as connection:
        after = connection.execute("select * from case_vectors order by alert_id").fetchall()

    # Only alerts with a summary are cases, and both fit in one request.
    assert first == {"cases": 2, "embedded": 2, "requests": 1}
    assert second == {"cases": 2, "embedded": 0, "requests": 0}
    assert len(embedder.requests) == 1
    assert after == before


def test_new_text_is_embedded_again(store, lake):  # noqa: F811
    alert_id = summarised(store, "t-1", "electronics", T0)
    embedder = FakeEmbedder()
    cases.index(store, embedder, lake)

    store.save_case_note(alert_id, "Card used in two countries within an hour.", "fake-model", "v1")
    assert cases.index(store, embedder, lake, [alert_id])["embedded"] == 1
    assert "two countries" in embedder.requests[-1][0]


def test_an_analyst_decision_wins_over_a_label_and_an_undo_clears_it(store, lake):  # noqa: F811
    alert_id = summarised(store, "20261001-0000001", "electronics", T0)
    labelled_at = pd.Timestamp(T0 + timedelta(days=60))
    write_deltalake(
        f"{lake}/{LABELS}",
        pd.DataFrame(
            {
                "transaction_id": ["20261001-0000001"],
                "is_fraud": [False],
                "label_source": ["dispute window closed"],
                "labelled_at": [labelled_at],
                "batch_id": ["2026-10-01"],
            }
        ),
    )

    cases.index(store, FakeEmbedder(), lake)
    assert row(store, alert_id)[:3] == (FALSE_POSITIVE, cases.LABEL, labelled_at.to_pydatetime())

    store.decide(alert_id, CONFIRMED)
    cases.index(store, FakeEmbedder(), lake, [alert_id])
    assert row(store, alert_id)[:2] == (CONFIRMED, cases.ANALYST)

    # Back to open, and the label is the only verdict left.
    with store.connect() as connection:
        connection.execute("update alerts set status = %s, decided_at = null where alert_id = %s", (OPEN, alert_id))
    cases.index(store, FakeEmbedder(), lake, [alert_id])
    assert row(store, alert_id)[:2] == (FALSE_POSITIVE, cases.LABEL)


def row(store, alert_id: int) -> tuple:  # noqa: F811
    with store.connect() as connection:
        found_row = connection.execute(
            "select outcome, outcome_source, known_at from case_vectors where alert_id = %s", (alert_id,)
        ).fetchone()
    return found_row["outcome"], found_row["outcome_source"], found_row["known_at"]


def test_a_simulated_outcome_never_becomes_a_label(store, lake, tmp_path, monkeypatch):  # noqa: F811
    """The demo answer lives in case_vectors only. The training labels come from alerts.status."""
    alert_id = summarised(store, "t-1", "electronics", T0)
    cases.index(store, FakeEmbedder(), lake)
    monkeypatch.setattr(ai_eval, "truth_for", lambda alerts, seed, accounts: {"t-1": True})

    assert cases.simulate(store, seed=7, accounts=100, count=10, now=T0) == 1
    assert row(store, alert_id) == (CONFIRMED, cases.SIMULATED, T0)
    assert store.get(alert_id)["status"] == OPEN
    assert export_decisions(store, str(tmp_path / "labels-lake")) == 0

    # An index run finds no real verdict, so the simulated one stays.
    cases.index(store, FakeEmbedder(), lake, [alert_id])
    assert row(store, alert_id)[:2] == (CONFIRMED, cases.SIMULATED)


def test_the_case_text_holds_the_payment_the_rule_and_the_note(store, lake):  # noqa: F811
    alert_id = summarised(store, "t-1", "electronics", T0)
    with store.connect() as connection:
        connection.execute("update alerts set policy_rules = %s where alert_id = %s", (["FP-9"], alert_id))
    store.save_case_note(alert_id, "Two countries in an hour.", "fake-model", "v1")

    text = cases.case_text(store.get(alert_id))

    assert "electronics payment of MYR 411.13, online, in US." in text
    assert "Policy: FP-9 " in text
    assert "Summary: A electronics payment abroad." in text
    assert "Case note: Two countries in an hour." in text
