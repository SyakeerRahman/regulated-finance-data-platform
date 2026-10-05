import numpy as np
import pandas as pd
import pytest

from finplat import model_report
from finplat.evaluation import CURVE_POINTS, curves, drift_level, psi

RNG = np.random.default_rng(7)


def test_a_perfect_ranking_scores_one():
    y = np.array([0] * 90 + [1] * 10)
    report = curves(y, y.astype(float))
    assert report["roc_auc"] == 1.0
    assert report["pr_auc"] == 1.0
    assert report["positives"] == 10


def test_the_threshold_table_counts_what_a_threshold_would_catch():
    y = np.array([0, 0, 1, 1])
    report = curves(y, [0.1, 0.6, 0.4, 0.9])
    at = {row["threshold"]: row for row in report["thresholds"]}

    # At 0.5 two rows alert: one fraud caught, one false alarm, one fraud missed.
    assert at[0.5]["precision"] == 0.5
    assert at[0.5]["recall"] == 0.5
    assert at[0.0]["recall"] == 1.0
    # Nothing scores 1.0, so nothing alerts and precision is undefined, not perfect.
    assert at[1.0]["precision"] is None


def test_a_curve_is_thinned_but_keeps_both_ends():
    y = RNG.integers(0, 2, 50_000)
    report = curves(y, RNG.random(50_000))
    assert len(report["roc"]["fpr"]) <= CURVE_POINTS
    assert report["roc"]["fpr"][0] == 0.0
    assert report["roc"]["fpr"][-1] == 1.0
    assert report["roc_auc"] == pytest.approx(0.5, abs=0.02)


def test_the_same_distribution_does_not_drift():
    reference = RNG.normal(size=20_000)
    assert psi(reference, RNG.normal(size=20_000)) < 0.02
    assert drift_level(psi(reference, RNG.normal(size=20_000))) == "stable"


def test_a_shifted_distribution_drifts():
    reference = RNG.normal(size=20_000)
    assert drift_level(psi(reference, RNG.normal(loc=1.0, size=20_000))) == "significant"


def test_a_flag_is_binned_by_its_values():
    """Deciles of a 0/1 column collapse to one edge. The flag must still show its change."""
    reference = (RNG.random(20_000) < 0.1).astype(float)
    current = (RNG.random(20_000) < 0.5).astype(float)
    assert psi(reference, current) > 0.25
    assert psi(reference, reference) == 0.0


def _payments(hours, rng, amount_scale=1.0):
    """Feature rows whose amount depends on the hour, as fraud makes it at night."""
    hours = np.asarray(hours)
    night = hours < 6
    amount = rng.lognormal(3.0, 0.5, len(hours)) * np.where(night, 3.0, 1.0) * amount_scale
    return pd.DataFrame({"hour": hours, "is_night": night.astype(float), "amount": amount, "prior_transactions": 0.0})


def _drift(live: pd.DataFrame, reference: pd.DataFrame) -> dict:
    report = model_report.drift(
        {"features": reference, "scores": reference["amount"].to_numpy()},
        live.assign(score=live["amount"]).to_dict("records"),
    )
    return {row["feature"]: row for row in report["features"]} | {"score": report["prediction"]}


def test_a_calm_hour_of_live_data_does_not_read_as_drift():
    """Seen on 2026-10-05: a few hours of live data against all 24 training hours read is_night at
    a PSI of 11. The training rows of the same hours are the fair reference (SCRUM-49)."""
    rng = np.random.default_rng(5)
    reference = _payments(rng.integers(0, 24, 40_000), rng)
    for hour in (2, 14):
        found = _drift(_payments(np.full(3_000, hour), rng), reference)
        assert max(found[name]["psi"] for name in ("hour", "is_night", "amount", "score")) < 0.1, hour


def test_real_drift_still_reads_as_drift_inside_one_hour():
    rng = np.random.default_rng(6)
    reference = _payments(rng.integers(0, 24, 40_000), rng)
    found = _drift(_payments(np.full(3_000, 14), rng, amount_scale=3.0), reference)
    assert found["amount"]["psi"] > 0.25
    assert found["score"]["psi"] > 0.25


def test_a_feature_that_grows_by_design_says_so():
    rng = np.random.default_rng(7)
    # As measured on 2026-10-05: training rows had 0 to 2 earlier payments, live rows 4 to 6.
    reference = _payments(rng.integers(0, 24, 5_000), rng).assign(prior_transactions=rng.integers(0, 3, 5_000))
    live = _payments(np.full(1_000, 14), rng).assign(prior_transactions=rng.integers(4, 7, 1_000))
    found = _drift(live, reference)
    assert found["prior_transactions"]["by_design"] and found["prior_transactions"]["reason"]
    assert found["prior_transactions"]["psi"] > 0.25
    assert not found["amount"]["by_design"]
