"""The Lambda scores with its own code, so it has to be proved equal to the real model.

These tests need a trained model in MLflow. They skip when no server answers, so a clone with no
containers running still gets a green suite.
"""

import json
from datetime import date

import pytest

from finplat.domain import FEATURE_COLUMNS
from finplat.features import row_features
from finplat.generate import generate
from finplat.settings import get_settings

pytest.importorskip("mlflow")

SAMPLE = 300


@pytest.fixture(scope="module")
def exported() -> dict:
    from finplat.export import export

    try:
        return export(get_settings().mlflow_tracking_uri)
    except Exception as error:  # noqa: BLE001 - any transport or registry failure means no server
        pytest.skip(f"no model in the registry: {error}")


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    transactions, _ = generate(date(2026, 9, 1), SAMPLE, seed=11, accounts=500)
    clean = transactions[transactions["account_id"].notna() & (transactions["amount"] > 0)]
    return [
        row_features(transaction, prior_count=position % 7, prior_mean=20.0 + position % 50)
        for position, transaction in enumerate(clean.to_dict("records"))
    ]


def test_the_lambda_scorer_matches_xgboost(exported, rows):
    """The rule this design rests on. A drift here means the Lambda and the live service
    disagree about the same transaction, and neither one would report a problem."""
    import pandas as pd

    from finplat.registry import load_production
    from lambda_fn.scorer import Scorer

    model = load_production(get_settings().mlflow_tracking_uri)
    expected = model.predict_proba(pd.DataFrame(rows)[FEATURE_COLUMNS].astype("float64"))[:, 1]

    scorer = Scorer(exported)
    for position, features in enumerate(rows):
        assert scorer.score(features) == pytest.approx(float(expected[position]), abs=1e-6), f"row {position} differs"


def test_the_handler_answers_a_post(exported, tmp_path, monkeypatch):
    from lambda_fn import handler as module
    from lambda_fn.scorer import Scorer

    monkeypatch.setattr(module, "_scorer", Scorer(exported))
    event = {
        "body": json.dumps(
            {
                "transaction": {
                    "transaction_id": "20260926-0015837",
                    "account_id": "ACC01518",
                    "amount": 411.13,
                    "ts": "2026-09-26T11:58:39+00:00",
                    "country": "US",
                    "channel": "online",
                    "merchant_category": "fuel",
                },
                "prior_transactions": 5,
                "prior_mean": 14.49,
            }
        )
    }
    response = module.handler(event)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["features"]["amount_vs_account"] == pytest.approx(411.13 / 14.49)
    assert 0.0 <= body["score"] <= 1.0
    assert body["alert"] is (body["score"] >= body["threshold"])


def test_a_missing_field_is_refused_not_guessed(exported, monkeypatch):
    from lambda_fn import handler as module
    from lambda_fn.scorer import Scorer

    monkeypatch.setattr(module, "_scorer", Scorer(exported))
    response = module.handler({"body": json.dumps({"transaction": {"transaction_id": "x"}})})

    assert response["statusCode"] == 400
    assert "missing" in json.loads(response["body"])["error"]
