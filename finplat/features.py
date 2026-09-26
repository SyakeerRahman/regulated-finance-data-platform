"""The one definition of a model feature.

The batch path builds features for a whole table at once. The live path builds them for a single
transaction as it arrives. Two implementations of the same arithmetic drift apart, and the model
then sees different numbers in production than it saw in training. So the rule stated in the
tests is that both must agree row for row.
"""

from datetime import datetime

from finplat.domain import FEATURE_COLUMNS, HOME_COUNTRY, RISKY_CATEGORIES

__all__ = ["FEATURE_COLUMNS", "AccountHistory", "frame_features", "hour_of", "row_features"]


def hour_of(ts) -> int:
    """The hour of a timestamp, whatever shape it arrives in.

    The live path holds a pandas Timestamp. A Lambda receives a string over HTTP and must not
    import pandas to read one field out of it.
    """
    if hasattr(ts, "hour"):
        return int(ts.hour)
    return datetime.fromisoformat(str(ts)).hour


def row_features(transaction: dict, prior_count: int, prior_mean: float | None) -> dict:
    """Features for one transaction, given what its account did before it.

    `prior_count` and `prior_mean` describe the account up to but not including this transaction.
    A caller that passes the account's average including this row leaks the present into the past.
    """
    hour = hour_of(transaction["ts"])
    amount = float(transaction["amount"])
    return {
        "amount": amount,
        "hour": hour,
        "is_night": hour < 6,
        "is_abroad": transaction["country"] != HOME_COUNTRY,
        "is_online": transaction["channel"] == "online",
        "is_risky_category": transaction["merchant_category"] in RISKY_CATEGORIES,
        # 1.0 for an account's first transaction: no history is not the same as unusual.
        "amount_vs_account": 1.0 if not prior_count or not prior_mean else amount / prior_mean,
        "prior_transactions": prior_count,
    }


def frame_features(tx):
    """Features for a whole table, in one pass. `tx` must already be in time order."""
    # Imported here and not at the top, so the Lambda package can carry this module without
    # numpy and pandas. A zip Lambda has 250 MB unzipped, and those two would spend most of it.
    import numpy as np
    import pandas as pd

    by_account = tx.groupby("account_id")["amount"]
    earlier = by_account.cumcount()
    # np.nan, not pd.NA: pd.NA in an integer series makes the column object dtype, and the
    # division then returns NAType, which cannot cast to float64.
    prior_mean = (by_account.cumsum() - tx["amount"]) / earlier.replace(0, np.nan)

    return pd.DataFrame(
        {
            "amount": tx["amount"],
            "hour": tx["ts"].dt.hour.astype("int32"),
            "is_night": tx["ts"].dt.hour < 6,
            "is_abroad": tx["country"] != HOME_COUNTRY,
            "is_online": tx["channel"] == "online",
            "is_risky_category": tx["merchant_category"].isin(RISKY_CATEGORIES),
            "amount_vs_account": (tx["amount"] / prior_mean).fillna(1.0),
            "prior_transactions": earlier.astype("int64"),
        }
    )


class AccountHistory:
    """What each account did before now, held in memory for the live path.

    Redis replaces this in stage E. The arithmetic stays here either way, so the swap does not
    touch the features.
    """

    def __init__(self) -> None:
        self._count: dict[str, int] = {}
        self._total: dict[str, float] = {}

    def prior(self, account_id: str) -> tuple[int, float | None]:
        count = self._count.get(account_id, 0)
        return count, (self._total[account_id] / count if count else None)

    def add(self, account_id: str, amount: float) -> None:
        self._count[account_id] = self._count.get(account_id, 0) + 1
        self._total[account_id] = self._total.get(account_id, 0.0) + amount

    def warm(self, transactions) -> int:
        """Seed the history from what the lake already holds.

        A scorer that starts cold sees every account as brand new, so amount_vs_account is 1.0 on
        every row and the model scores a distribution it never trained on. The live path must
        start from the same history the batch path ended with.
        """
        by_account = transactions.groupby("account_id")["amount"]
        self._count |= by_account.count().astype(int).to_dict()
        self._total |= by_account.sum().astype(float).to_dict()
        return len(self._count)
