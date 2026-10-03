from datetime import date

import pandas as pd
import pytest

from finplat.pipeline import training_frame
from finplat.train import (
    MIN_FRAUD,
    MIN_HONEST,
    NOT_FEATURES,
    NotEnoughLabels,
    check_labels,
    features_of,
    fit,
    split_by_time,
)
from tests.test_pipeline import run_day

CUTOFF = pd.Timestamp("2027-01-01", tz="UTC")


@pytest.fixture
def judged(tmp_path) -> pd.DataFrame:
    lake = str(tmp_path / "lake")
    for day in (1, 2, 3, 4):
        run_day(lake, date(2026, 9, day), rows=20_000)
    return training_frame(lake, CUTOFF)


def test_no_identifier_or_answer_reaches_the_model(judged):
    columns = features_of(judged)
    assert set(columns).isdisjoint(NOT_FEATURES)
    assert "amount_vs_account" in columns


def test_the_split_is_by_time_not_at_random(judged):
    train, test = split_by_time(judged)
    assert train["ts"].max() <= test["ts"].min()
    assert len(test) == pytest.approx(len(judged) * 0.2, rel=0.01)


def test_the_model_beats_guessing(judged):
    train, test = split_by_time(judged)
    result = fit(train, test)
    # A model that guesses at random scores about the fraud rate, near 0.015.
    assert result.metrics["pr_auc"] > 0.20
    assert result.metrics["recall"] > 0.30
    assert 0.0 < result.threshold < 1.0


def test_the_model_never_sees_the_answer(judged):
    train, test = split_by_time(judged)
    result = fit(train, test)
    assert "is_fraud" not in result.features
    # A leaked target shows up as a near-perfect score, so this is the tripwire for it.
    assert result.metrics["pr_auc"] < 0.99


def test_the_logged_curves_are_the_test_set_the_metrics_came_from(judged):
    """The curves are kept because the test set is not. They must describe that same test set."""
    train, test = split_by_time(judged)
    result = fit(train, test)
    assert result.curves["rows"] == len(test)
    assert result.curves["pr_auc"] == pytest.approx(result.metrics["pr_auc"])
    assert result.curves["positives"] == int(test["is_fraud"].sum())


def test_a_cutoff_with_no_verdicts_yet_says_so(judged):
    """Seen live: the weekly run met an empty table and crashed with an IndexError in the metrics."""
    with pytest.raises(NotEnoughLabels, match="0 fraud and 0 honest"):
        check_labels(judged.iloc[0:0])


def test_too_few_fraud_cases_is_not_enough(judged):
    honest = judged[~judged["is_fraud"]]
    few = pd.concat([honest, judged[judged["is_fraud"]].head(MIN_FRAUD - 1)])
    with pytest.raises(NotEnoughLabels, match=f"{MIN_FRAUD - 1} fraud and"):
        check_labels(few)


def test_fraud_alone_is_not_enough():
    """For weeks after a fresh start only chargebacks are known. A model of fraud alone learns nothing."""
    with pytest.raises(NotEnoughLabels, match=f"Training needs {MIN_FRAUD} and {MIN_HONEST:,}"):
        check_labels(pd.DataFrame({"is_fraud": [True] * 5_000}))


def test_four_days_of_verdicts_are_enough(judged):
    check_labels(judged)
