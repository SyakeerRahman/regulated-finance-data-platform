import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from finplat.model_report import drift, importance

RNG = np.random.default_rng(7)


def test_importance_shares_add_up_and_the_signal_leads():
    x = pd.DataFrame({"signal": RNG.normal(size=2_000), "noise": RNG.normal(size=2_000)})
    y = (x["signal"] > 0.5).astype(int)
    model = XGBClassifier(n_estimators=20, max_depth=3).fit(x, y)

    shares = importance(model)
    assert shares[0][0] == "signal"
    assert sum(share for _, share in shares) == 1.0 or abs(sum(share for _, share in shares) - 1.0) < 1e-9


def test_drift_names_the_feature_that_moved():
    reference = {
        "features": pd.DataFrame({"amount": RNG.normal(50, 10, 5_000), "is_abroad": (RNG.random(5_000) < 0.1) * 1.0}),
        "scores": RNG.random(5_000) * 0.1,
    }
    live = [{"amount": value, "is_abroad": 0.0, "score": 0.05} for value in RNG.normal(90, 10, 2_000)]

    report = drift(reference, live)
    assert report["features"][0]["feature"] == "amount"
    assert report["features"][0]["level"] == "significant"
    assert report["live_rows"] == 2_000


def test_no_live_rows_means_no_drift_verdict():
    reference = {"features": pd.DataFrame({"amount": [1.0, 2.0]}), "scores": np.array([0.1, 0.2])}
    assert drift(reference, [])["prediction"] is None
