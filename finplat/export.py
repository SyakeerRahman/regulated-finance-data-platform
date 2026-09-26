"""Export the live model as plain JSON, so a Lambda can score without XGBoost.

    uv run python -m finplat.export lambda_fn/model.json

A zip Lambda has 250 MB unzipped. XGBoost and its shared library spend most of that on their own,
and importing them costs seconds on a cold start. A gradient boosted tree is only a list of
if-then branches, so the trees travel as JSON and `lambda_fn/scorer.py` walks them in about 60
lines of standard library. The cold start falls from seconds to milliseconds.

`tests/test_lambda.py` scores the same rows through both and asserts they agree, because two
implementations of one model is exactly the shape of a silent production bug.
"""

import json
import sys
from pathlib import Path

from finplat.domain import FEATURE_COLUMNS
from finplat.registry import live_version, load_production, production_threshold
from finplat.settings import get_settings


def export(tracking_uri: str) -> dict:
    """The live model as a dictionary: its trees, its intercept, and the alert threshold."""
    booster = load_production(tracking_uri).get_booster()
    config = json.loads(booster.save_config())["learner"]["learner_model_param"]

    return {
        "version": live_version(tracking_uri),
        "features": FEATURE_COLUMNS,
        "threshold": production_threshold(tracking_uri),
        # XGBoost stores this as a probability. The trees add to a margin, so the intercept that
        # the margin starts from is its logit.
        "base_score": _base_score(config["base_score"]),
        "trees": [json.loads(tree) for tree in booster.get_dump(dump_format="json")],
    }


def _base_score(raw: str) -> float:
    """XGBoost 3 reports this as a bracketed vector, for example '[5E-1]'."""
    return float(str(raw).strip("[]").split(",")[0])


def main(destination: str) -> None:
    model = export(get_settings().mlflow_tracking_uri)
    path = Path(destination)
    path.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    size = path.stat().st_size / 1024
    print(f"version {model['version']}, {len(model['trees'])} trees, {size:.0f} KB -> {path}")


if __name__ == "__main__":
    main(sys.argv[1])
