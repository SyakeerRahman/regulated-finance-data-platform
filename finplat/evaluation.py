"""How good a model is, and whether the payments it sees now look like the ones it learned from.

`curves` runs at training time and its output is logged next to the model, because the test set
does not survive: retention trims the lake and a rebuild regenerates it, so the rows a version
was tested on are gone a few weeks later. `psi` compares the live stream with the training data.
"""

import numpy as np

CURVE_POINTS = 200
THRESHOLD_STEPS = 51
PSI_BINS = 10
# The usual reading of the population stability index.
PSI_MODERATE = 0.1
PSI_SIGNIFICANT = 0.25


def curves(y_true, scored) -> dict:
    """ROC and precision-recall curves, and precision, recall and F1 at a range of thresholds."""
    from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score, roc_curve

    y_true = np.asarray(y_true).astype(int)
    scored = np.asarray(scored, dtype="float64")

    fpr, tpr, _ = roc_curve(y_true, scored)
    precision, recall, _ = precision_recall_curve(y_true, scored)
    fpr, tpr = _thin(fpr, tpr)
    recall, precision = _thin(recall[::-1], precision[::-1])

    table = []
    positives = int(y_true.sum())
    for threshold in np.linspace(0, 1, THRESHOLD_STEPS):
        flagged = scored >= threshold
        caught = int((flagged & (y_true == 1)).sum())
        # No alerts at all has no precision. None, not 1.0, so a chart leaves a gap.
        p = caught / int(flagged.sum()) if flagged.any() else None
        r = caught / positives if positives else None
        f1 = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
        table.append({"threshold": round(float(threshold), 2), "precision": p, "recall": r, "f1": f1})

    return {
        "rows": len(y_true),
        "positives": positives,
        "roc_auc": float(roc_auc_score(y_true, scored)),
        "pr_auc": float(average_precision_score(y_true, scored)),
        "roc": {"fpr": fpr, "tpr": tpr},
        "pr": {"recall": recall, "precision": precision},
        "thresholds": table,
    }


def psi(reference, current, bins: int = PSI_BINS, weights=None) -> float:
    """Population stability index of `current` against `reference`.

    Bins are the reference's deciles, so each holds a tenth of the training data. A feature with
    few values (a 0/1 flag) gets one bin for each value instead.

    `weights` gives each reference row a weight, so the reference can be made to look like the
    live sample in one respect, such as the hours it covers, before the rest is compared.
    """
    reference = np.asarray(reference, dtype="float64")
    current = np.asarray(current, dtype="float64")
    if len(reference) == 0 or len(current) == 0:
        return 0.0
    weights = np.ones(len(reference)) if weights is None else np.asarray(weights, dtype="float64")

    values = np.unique(reference)
    if len(values) <= bins:
        # A value seen live and never in training still counts, in a bin of its own.
        edges = np.concatenate([[-np.inf], (values[:-1] + values[1:]) / 2, [np.inf]])
    else:
        inner = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)[1:-1]))
        edges = np.concatenate([[-np.inf], inner, [np.inf]])

    expected = np.histogram(reference, edges, weights=weights)[0] / weights.sum()
    actual = np.histogram(current, edges)[0] / len(current)
    # An empty bin would make the log infinite. A small floor keeps the index finite and large.
    expected = np.clip(expected, 1e-4, None)
    actual = np.clip(actual, 1e-4, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def drift_level(value: float) -> str:
    if value >= PSI_SIGNIFICANT:
        return "significant"
    if value >= PSI_MODERATE:
        return "moderate"
    return "stable"


def _thin(x, y, points: int = CURVE_POINTS) -> tuple[list[float], list[float]]:
    """At most `points` points, always keeping both ends. 20,000 points draw the same line."""
    if len(x) > points:
        keep = np.unique(np.linspace(0, len(x) - 1, points).round().astype(int))
        x, y = np.asarray(x)[keep], np.asarray(y)[keep]
    return [round(float(value), 5) for value in x], [round(float(value), 5) for value in y]
