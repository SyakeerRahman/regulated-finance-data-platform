"""Scoring the AI analyst against the truth. No model is called here: the rebuild and the arithmetic are tested."""

from datetime import date

import pytest

from finplat.ai_eval import score, truth_for
from finplat.alerts import LIKELY_FALSE_POSITIVE, LIKELY_FRAUD, UNSURE
from finplat.generate import generate

DAY = date(2026, 10, 3)


def alerts_from(round_: int, count: int) -> tuple[list[dict], dict[str, bool]]:
    transactions, labels = generate(DAY, 300, 7, 500, round_=round_)
    rows = transactions.drop_duplicates("transaction_id").head(count)
    answer = labels.set_index("transaction_id")["is_fraud"]
    alerts = [{"transaction_id": row.transaction_id, "amount": abs(row.amount)} for row in rows.itertuples()]
    return alerts, {alert["transaction_id"]: bool(answer[alert["transaction_id"]]) for alert in alerts}


def test_the_truth_is_rebuilt_from_the_id_alone():
    first, expected_first = alerts_from(0, 20)
    later, expected_later = alerts_from(3, 20)

    truth = truth_for(first + later, seed=7, accounts=500, rows=300)

    assert truth == expected_first | expected_later


def test_an_alert_the_generator_cannot_rebuild_is_left_out():
    """A payment from an older generator has another amount. A guessed answer would be a wrong key."""
    alerts, _ = alerts_from(0, 3)
    alerts[0] = {**alerts[0], "amount": alerts[0]["amount"] + 5}
    alerts.append({"transaction_id": "not-an-id", "amount": 1.0})

    truth = truth_for(alerts, seed=7, accounts=500, rows=300)

    assert alerts[0]["transaction_id"] not in truth
    assert len(truth) == 2


def test_the_score_compares_the_ai_with_always_saying_fraud():
    pairs = [
        (LIKELY_FRAUD, True),
        (LIKELY_FRAUD, True),
        (LIKELY_FRAUD, False),
        (LIKELY_FALSE_POSITIVE, False),
        (LIKELY_FALSE_POSITIVE, True),
        (UNSURE, True),
    ]

    result = score(pairs)

    assert result["alerts"] == 6
    assert result["baseline_accuracy"] == pytest.approx(4 / 6)
    # Unsure is left out of accuracy and counted in coverage.
    assert result["accuracy"] == pytest.approx(3 / 5)
    assert result["coverage"] == pytest.approx(5 / 6)
    assert result["fraud_precision"] == pytest.approx(2 / 3)
    assert result["false_positive_precision"] == pytest.approx(1 / 2)
    assert result["fraud_recall"] == pytest.approx(2 / 4)
    assert result["false_positives_caught"] == pytest.approx(1 / 2)
    assert result["matrix"][UNSURE] == {"fraud": 1, "honest": 0}


def test_an_empty_score_has_no_rates_rather_than_a_division_error():
    assert score([])["accuracy"] is None
