"""Train a fraud model on the features whose verdict was already known.

uv run python -m finplat.train 2027-01-01

The date is the cutoff. The run may only use a label that arrived on or before it, because a
model in production cannot read a chargeback that has not happened yet.
"""

import sys
from dataclasses import dataclass
from datetime import date

import mlflow
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve
from xgboost import XGBClassifier

from finplat.pipeline import training_frame
from finplat.settings import get_settings

EXPERIMENT = "fraud"
REGISTERED_MODEL = "fraud_model"

# Not features: two identifiers, the time the split is made on, and the answer itself.
NOT_FEATURES = ["transaction_id", "account_id", "ts", "is_fraud"]

# The newest fifth of the judged rows is the test set. A random split would let the model learn
# from Thursday to predict Wednesday, and no production model ever gets that.
HOLDOUT = 0.2

PARAMS = {
    "n_estimators": 300,
    "max_depth": 5,
    "learning_rate": 0.08,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "eval_metric": "aucpr",
}


@dataclass
class Result:
    model: XGBClassifier
    metrics: dict[str, float]
    threshold: float
    features: list[str]


def features_of(frame: pd.DataFrame) -> list[str]:
    return [c for c in frame.columns if c not in NOT_FEATURES]


def split_by_time(frame: pd.DataFrame, holdout: float = HOLDOUT) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The oldest rows train the model. The newest rows test it."""
    ordered = frame.sort_values("ts", ignore_index=True)
    cut = int(len(ordered) * (1 - holdout))
    return ordered.iloc[:cut], ordered.iloc[cut:]


def fit(train: pd.DataFrame, test: pd.DataFrame) -> Result:
    """Fit one model and measure it. No MLflow here, so a test needs no server."""
    columns = features_of(train)
    x_train, y_train = train[columns].astype("float64"), train["is_fraud"].astype("int32")
    x_test, y_test = test[columns].astype("float64"), test["is_fraud"].astype("int32")

    # Fraud is about 1.5% of rows. Without this weight the model learns to answer "never".
    negative, positive = int((y_train == 0).sum()), int((y_train == 1).sum())
    model = XGBClassifier(**PARAMS, scale_pos_weight=negative / max(positive, 1))
    model.fit(x_train, y_train)

    scored = model.predict_proba(x_test)[:, 1]
    precision, recall, cuts = precision_recall_curve(y_test, scored)
    # precision_recall_curve returns one more point than it returns thresholds.
    f1 = 2 * precision[:-1] * recall[:-1] / (precision[:-1] + recall[:-1] + 1e-12)
    best = int(f1.argmax())

    return Result(
        model=model,
        metrics={
            # PR-AUC does not depend on a threshold, so it is the number to compare runs on.
            # Accuracy is useless here: "never fraud" scores 98.5%.
            "pr_auc": float(average_precision_score(y_test, scored)),
            "precision": float(precision[best]),
            "recall": float(recall[best]),
            "f1": float(f1[best]),
            "train_rows": float(len(train)),
            "test_rows": float(len(test)),
            "fraud_rate": float(y_train.mean()),
        },
        threshold=float(cuts[best]),
        features=columns,
    )


def train_and_log(lake: str, as_of: pd.Timestamp, tracking_uri: str) -> Result:
    """Fit a model and record the run, so a later reader can tell which model decided what."""
    frame = training_frame(lake, as_of)
    train, test = split_by_time(frame)
    result = fit(train, test)

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run():
        mlflow.log_params(PARAMS | {"label_cutoff": as_of.date().isoformat(), "holdout": HOLDOUT})
        mlflow.log_metrics(result.metrics)
        mlflow.log_param("threshold", result.threshold)
        mlflow.xgboost.log_model(
            result.model,
            name="model",
            input_example=test[result.features].head(3).astype("float64"),
            registered_model_name=REGISTERED_MODEL,
        )
    return result


def main(as_of: date) -> None:
    settings = get_settings()
    result = train_and_log(settings.lake_uri, pd.Timestamp(as_of, tz="UTC"), settings.mlflow_tracking_uri)
    for name, value in result.metrics.items():
        print(f"{name:<12} {value:.4f}")
    print(f"{'threshold':<12} {result.threshold:.4f}")


if __name__ == "__main__":
    # MLflow prints a run link with an emoji in it. The Windows console is cp1252 and stops on it.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main(date.fromisoformat(sys.argv[1]))
