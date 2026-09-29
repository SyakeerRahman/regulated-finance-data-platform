"""What the Model tab shows: every version with its numbers, its curves, what it leans on, and
whether the live stream still looks like the data it learned from."""

import mlflow
import pandas as pd
from mlflow import MlflowClient

from finplat.evaluation import curves, drift_level, psi
from finplat.pipeline import training_frame
from finplat.train import EVALUATION_ARTIFACT, REGISTERED_MODEL, features_of, split_by_time

METRICS = ("pr_auc", "roc_auc", "precision", "recall", "f1", "train_rows", "test_rows")


def version_rows(tracking_uri: str) -> list[dict]:
    """Every registered version, newest first, with numbers rather than formatted strings."""
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient()
    aliases: dict[str, list[str]] = {}
    for alias, version in client.get_registered_model(REGISTERED_MODEL).aliases.items():
        aliases.setdefault(version, []).append(alias)

    rows = []
    for version in client.search_model_versions(f"name = '{REGISTERED_MODEL}'"):
        run = client.get_run(version.run_id).data
        rows.append(
            {
                "version": version.version,
                "run_id": version.run_id,
                "aliases": sorted(aliases.get(version.version, [])),
                "created": version.creation_timestamp,
                "metrics": {name: run.metrics[name] for name in METRICS if name in run.metrics},
                "threshold": float(run.params["threshold"]) if "threshold" in run.params else None,
                "label_cutoff": run.params.get("label_cutoff"),
                "params": {name: run.params.get(name) for name in ("n_estimators", "max_depth", "learning_rate")},
            }
        )
    return sorted(rows, key=lambda row: int(row["version"]), reverse=True)


def evaluation(tracking_uri: str, lake: str, row: dict) -> dict:
    """The curves logged at training time, or, for a version trained before they were logged,
    the same curves recomputed on the lake as it is now. The answer says which it is."""
    mlflow.set_tracking_uri(tracking_uri)
    logged = {item.path for item in MlflowClient().list_artifacts(row["run_id"])}
    if EVALUATION_ARTIFACT in logged:
        return {"source": "training", **mlflow.artifacts.load_dict(f"runs:/{row['run_id']}/{EVALUATION_ARTIFACT}")}

    model = mlflow.xgboost.load_model(f"models:/{REGISTERED_MODEL}/{row['version']}")
    _, test = holdout(lake, row["label_cutoff"])
    scored = model.predict_proba(test[features_of(test)].astype("float64"))[:, 1]
    return {"source": "recomputed", **curves(test["is_fraud"].astype(int), scored)}


def holdout(lake: str, label_cutoff: str | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The split training makes, on the lake as it is today."""
    cutoff = pd.Timestamp(label_cutoff or "2100-01-01", tz="UTC")
    return split_by_time(training_frame(lake, cutoff))


def importance(model) -> list[list]:
    """Each feature's share of the total gain, largest first. Gain is how much a feature's
    splits improved the trees, which says more than how often it was split on."""
    gain = model.get_booster().get_score(importance_type="gain")
    total = sum(gain.values()) or 1.0
    return sorted(([name, value / total] for name, value in gain.items()), key=lambda pair: -pair[1])


def reference(model, lake: str, label_cutoff: str | None) -> dict:
    """The training data the live stream is compared with: the features and the scores."""
    train, _ = holdout(lake, label_cutoff)
    columns = features_of(train)
    features = train[columns].astype("float64")
    return {"features": features, "scores": model.predict_proba(features)[:, 1]}


def drift(reference: dict, live: list[dict]) -> dict:
    """PSI of every feature and of the score, live against training."""
    if not live:
        return {"live_rows": 0, "reference_rows": len(reference["features"]), "features": [], "prediction": None}
    current = pd.DataFrame(live)
    features = [
        {"feature": name, "psi": value, "level": drift_level(value)}
        for name in reference["features"].columns
        if name in current
        for value in [psi(reference["features"][name], current[name])]
    ]
    score = psi(reference["scores"], current["score"])
    return {
        "live_rows": len(current),
        "reference_rows": len(reference["features"]),
        "features": sorted(features, key=lambda row: -row["psi"]),
        "prediction": {"psi": score, "level": drift_level(score)},
    }
