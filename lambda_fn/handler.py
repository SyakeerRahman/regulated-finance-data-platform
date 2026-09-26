"""AWS Lambda: score one transaction.

    POST { "transaction": {...}, "prior_transactions": 5, "prior_mean": 14.49 }
    ->    { "score": 0.9412, "alert": true, "model_version": "3", "features": {...} }

The caller supplies the account history, because a Lambda has no state and reading the lake on
every request would cost more than the score is worth. The live service in `api/` holds that
history and is the normal caller.
"""

import json
from importlib.resources import files

from finplat.features import row_features
from lambda_fn.scorer import Scorer


def _load() -> Scorer | None:
    """Read the model through the package, not through a file path.

    Path(__file__).exists() is False when the code runs from a zip, so a plain file read works
    in a checkout and returns nothing in the artifact that actually ships.
    """
    try:
        return Scorer(json.loads(files("lambda_fn").joinpath("model.json").read_text(encoding="utf-8")))
    except (FileNotFoundError, ModuleNotFoundError):
        return None


# Loaded once per container, not once per request. AWS reuses a warm container, so this cost is
# paid on a cold start only.
_scorer = _load()

REQUIRED = ("transaction_id", "account_id", "amount", "ts", "country", "channel", "merchant_category")


def score_request(body: dict) -> dict:
    transaction = body.get("transaction") or {}
    missing = [field for field in REQUIRED if field not in transaction]
    if missing:
        raise ValueError(f"transaction is missing {', '.join(missing)}")

    features = row_features(
        transaction,
        prior_count=int(body.get("prior_transactions", 0)),
        prior_mean=body.get("prior_mean"),
    )
    score = _scorer.score(features)
    return {
        "transaction_id": transaction["transaction_id"],
        "score": round(score, 4),
        "alert": score >= _scorer.threshold,
        "threshold": round(_scorer.threshold, 4),
        "model_version": _scorer.version,
        "features": features,
    }


def handler(event, _context=None) -> dict:
    """The Lambda entry point. It accepts a direct invoke and a Function URL POST."""
    body = event.get("body")
    payload = json.loads(body) if isinstance(body, str) else (body or event)

    try:
        result = score_request(payload)
    except (ValueError, KeyError, TypeError) as error:
        return {"statusCode": 400, "body": json.dumps({"error": str(error)})}

    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(result),
    }
