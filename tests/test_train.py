from datetime import date

import pandas as pd
import pytest

from finplat.pipeline import training_frame
from finplat.train import NOT_FEATURES, features_of, fit, split_by_time
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
