"""Which model version is live.

    uv run python -m finplat.registry list
    uv run python -m finplat.registry promote 2

MLflow 3 removed the old stage names, so a live version carries an alias instead. The scorer
loads `models:/fraud_model@production` and never a file path, so a promotion changes the live
model with no deploy and a rollback is one command.
"""

import sys

import mlflow
from mlflow import MlflowClient

from finplat.settings import get_settings
from finplat.train import REGISTERED_MODEL

PRODUCTION = "production"
MODEL_URI = f"models:/{REGISTERED_MODEL}@{PRODUCTION}"


def _client(tracking_uri: str) -> MlflowClient:
    mlflow.set_tracking_uri(tracking_uri)
    return MlflowClient()


def versions(tracking_uri: str) -> list[dict[str, str]]:
    """Every registered version, newest first, with the metrics of the run that made it."""
    client = _client(tracking_uri)
    # A version object from a search does not carry its aliases. The registered model holds them.
    by_version: dict[str, list[str]] = {}
    for alias, version in client.get_registered_model(REGISTERED_MODEL).aliases.items():
        by_version.setdefault(version, []).append(alias)

    rows = []
    for version in client.search_model_versions(f"name = '{REGISTERED_MODEL}'"):
        metrics = client.get_run(version.run_id).data.metrics
        rows.append(
            {
                "version": version.version,
                "pr_auc": f"{metrics.get('pr_auc', float('nan')):.4f}",
                "precision": f"{metrics.get('precision', float('nan')):.4f}",
                "recall": f"{metrics.get('recall', float('nan')):.4f}",
                "alias": ", ".join(sorted(by_version.get(version.version, []))) or "-",
            }
        )
    return sorted(rows, key=lambda row: int(row["version"]), reverse=True)


def promote(tracking_uri: str, version: str) -> None:
    """Point the production alias at one version. The scorer picks it up on its next load."""
    _client(tracking_uri).set_registered_model_alias(REGISTERED_MODEL, PRODUCTION, version)


def live_version(tracking_uri: str) -> str | None:
    client = _client(tracking_uri)
    try:
        return client.get_model_version_by_alias(REGISTERED_MODEL, PRODUCTION).version
    except mlflow.exceptions.MlflowException:
        return None


def load_production(tracking_uri: str):
    """The live model, as the classifier itself.

    The pyfunc wrapper around an XGBClassifier answers with a class label, so it says 1.0 and
    never 0.94. A scorer needs the probability, so it loads the xgboost flavour and calls
    predict_proba.
    """
    mlflow.set_tracking_uri(tracking_uri)
    return mlflow.xgboost.load_model(MODEL_URI)


def main(argv: list[str]) -> None:
    uri = get_settings().mlflow_tracking_uri
    if argv[:1] == ["promote"]:
        promote(uri, argv[1])
        print(f"{REGISTERED_MODEL} version {argv[1]} is now {PRODUCTION}")
        return
    print(f"{'version':<8}{'pr_auc':<10}{'precision':<12}{'recall':<10}alias")
    for row in versions(uri):
        print(f"{row['version']:<8}{row['pr_auc']:<10}{row['precision']:<12}{row['recall']:<10}{row['alias']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main(sys.argv[1:])
