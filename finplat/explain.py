"""Why the model scored a transaction the way it did.

A score of 0.94 with no reason is not usable. A fraud analyst has to act on it, and in a
regulated setting the customer can ask why their card was refused. SHAP opens the trees and
reports how much each feature moved the score, so every alert carries its own explanation.
"""

from dataclasses import dataclass

# What each feature is called in a sentence a person reads. A feature name is not an
# explanation: "is_risky_category true" tells an analyst nothing they can act on.
PHRASES = {
    "amount_vs_account": ("{value:.0f}x this account's normal spend", "well below this account's normal spend"),
    "is_abroad": ("a purchase outside Malaysia", "a purchase inside Malaysia"),
    "is_online": ("card not present", "the card was present"),
    "is_night": ("between midnight and 6am", "during the day"),
    "is_risky_category": ("a category that resells easily", "an everyday category"),
    "prior_transactions": ("a thin account history", "a long account history"),
    "amount": ("a large amount", "a small amount"),
    "hour": ("the time of day", "the time of day"),
}


@dataclass
class Reason:
    feature: str
    contribution: float
    phrase: str

    @property
    def raises_score(self) -> bool:
        return self.contribution > 0


class Explainer:
    """SHAP over the live model. Built once, because building it walks every tree."""

    def __init__(self, model, features: list[str]) -> None:
        import shap

        self.features = features
        self._explainer = shap.TreeExplainer(model)

    def reasons(self, values: dict, top: int = 3) -> list[Reason]:
        """The features that moved this score the most, largest first."""
        import pandas as pd

        row = pd.DataFrame([values])[self.features].astype("float64")
        contributions = self._explainer.shap_values(row)[0]

        ranked = sorted(zip(self.features, contributions, strict=True), key=lambda pair: -abs(pair[1]))
        return [
            Reason(feature=name, contribution=float(amount), phrase=phrase_for(name, values[name], amount > 0))
            for name, amount in ranked[:top]
        ]


def phrase_for(feature: str, value, raises_score: bool) -> str:
    up, down = PHRASES.get(feature, (feature, feature))
    template = up if raises_score else down
    return template.format(value=float(value))


def sentence(reasons: list[Reason]) -> str:
    """One line an analyst can read, built only from the reasons that raised the score."""
    raising = [reason.phrase for reason in reasons if reason.raises_score]
    if not raising:
        return "Nothing in this transaction stands out."
    if len(raising) == 1:
        return f"{_upper_first(raising[0])}."
    return f"{_upper_first(', '.join(raising[:-1]))}, and {raising[-1]}."


def _upper_first(text: str) -> str:
    """Raise the first letter only. str.capitalize lowercases the rest, so Malaysia loses its M."""
    return text[:1].upper() + text[1:]
