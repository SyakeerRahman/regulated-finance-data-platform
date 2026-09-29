import numpy as np
import pytest

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
