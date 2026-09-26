"""Score one transaction by walking the exported trees. Standard library only.

A gradient boosted tree is a list of if-then branches. Each tree answers with a leaf value, the
values add to a margin, and a sigmoid turns the margin into a probability. That is the whole of
it, so the Lambda carries this file instead of XGBoost.
"""

import json
import math
from pathlib import Path


class Scorer:
    def __init__(self, model: dict) -> None:
        self.features: list[str] = model["features"]
        self.trees: list[dict] = model["trees"]
        self.threshold: float = model["threshold"]
        self.version: str = model["version"]
        base = model["base_score"]
        # The trees add to a margin, so the starting point is the logit of the base probability.
        self.intercept = math.log(base / (1 - base)) if 0 < base < 1 else 0.0

    @classmethod
    def load(cls, path: str | Path) -> "Scorer":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def margin(self, features: dict) -> float:
        return self.intercept + sum(_walk(tree, features) for tree in self.trees)

    def score(self, features: dict) -> float:
        """The probability that this transaction is fraud."""
        return 1.0 / (1.0 + math.exp(-self.margin(features)))


def _walk(node: dict, features: dict) -> float:
    """Follow one tree from its root to a leaf."""
    while "leaf" not in node:
        value = features.get(node["split"])
        # XGBoost sends a missing value down the branch it learned to prefer.
        if value is None:
            nxt = node["missing"]
        else:
            nxt = node["yes"] if float(value) < node["split_condition"] else node["no"]
        node = next(child for child in node["children"] if child["nodeid"] == nxt)
    return float(node["leaf"])
