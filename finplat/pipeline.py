"""Bronze, silver and gold Delta tables for card transactions.

Every step can run again for the same batch and leave the tables as one run would. Airflow retries
a failed task, and in a regulated setting a retry that counts a payment twice is an incident.
"""

import numpy as np
import pandas as pd
from deltalake import DeltaTable, write_deltalake

from finplat.generate import HOME_COUNTRY, RISKY_CATEGORIES

BRONZE = "bronze/transactions"
QUARANTINE = "silver/quarantine"
SILVER = "silver/transactions"
LABELS = "silver/labels"
GOLD = "gold/transaction_features"


def _replace_batch(path: str, frame: pd.DataFrame, batch_id: str) -> None:
    """Write one batch, replacing any earlier copy of it, and leave other batches alone."""
    # Without the reset, pandas writes its own row numbers into the table as __index_level_0__.
    frame = frame.reset_index(drop=True)
    if DeltaTable.is_deltatable(path):
        write_deltalake(path, frame, mode="overwrite", predicate=f"batch_id = '{batch_id}'")
    else:
        write_deltalake(path, frame, partition_by=["batch_id"])


def load_bronze(lake: str, batch: pd.DataFrame, batch_id: str) -> int:
    """Land the batch exactly as received. Nothing is fixed here, so an auditor can see the original."""
    frame = batch.assign(batch_id=batch_id, ingested_at=pd.Timestamp.now(tz="UTC"))
    _replace_batch(f"{lake}/{BRONZE}", frame, batch_id)
    return len(frame)


def load_labels(lake: str, labels: pd.DataFrame, batch_id: str) -> int:
    """Store the verdicts. Each one carries the date a bank would really learn it."""
    _replace_batch(f"{lake}/{LABELS}", labels.assign(batch_id=batch_id), batch_id)
    return len(labels)


def refine_silver(lake: str, batch_id: str) -> dict[str, int]:
    """Validate one bronze batch. Bad rows go to quarantine with a reason, good rows are upserted."""
    bronze = DeltaTable(f"{lake}/{BRONZE}").to_pandas(filters=[("batch_id", "=", batch_id)])
    bronze = bronze.drop(columns="ingested_at")

    reason = pd.Series(
        np.select(
            [bronze["account_id"].isna(), bronze["amount"] <= 0],
            ["missing account", "non-positive amount"],
            default="",
        ),
        index=bronze.index,
    )
    rejected = bronze[reason != ""].assign(reason=reason[reason != ""])
    accepted = bronze[reason == ""].drop_duplicates("transaction_id").reset_index(drop=True)

    if len(rejected):
        _replace_batch(f"{lake}/{QUARANTINE}", rejected, batch_id)

    silver = f"{lake}/{SILVER}"
    if DeltaTable.is_deltatable(silver):
        (
            DeltaTable(silver)
            .merge(accepted, predicate="t.transaction_id = s.transaction_id", source_alias="s", target_alias="t")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute()
        )
    else:
        write_deltalake(silver, accepted)

    return {
        "received": len(bronze),
        "quarantined": len(rejected),
        "duplicates": int((reason == "").sum()) - len(accepted),
        "accepted": len(accepted),
    }


def build_gold(lake: str) -> int:
    """Model features for every silver transaction, rebuilt in full. No target column lives here."""
    tx = DeltaTable(f"{lake}/{SILVER}").to_pandas().sort_values(["ts", "transaction_id"], ignore_index=True)

    # The account's average before this transaction, never after it. A feature that sees the future
    # scores well in training and fails in production.
    by_account = tx.groupby("account_id")["amount"]
    earlier = by_account.cumcount()
    prior_mean = (by_account.cumsum() - tx["amount"]) / earlier.replace(0, np.nan)

    gold = pd.DataFrame(
        {
            "transaction_id": tx["transaction_id"],
            "account_id": tx["account_id"],
            "ts": tx["ts"],
            "amount": tx["amount"],
            "hour": tx["ts"].dt.hour.astype("int32"),
            "is_night": tx["ts"].dt.hour < 6,
            "is_abroad": tx["country"] != HOME_COUNTRY,
            "is_online": tx["channel"] == "online",
            "is_risky_category": tx["merchant_category"].isin(RISKY_CATEGORIES),
            # 1.0 for an account's first transaction: no history is not the same as unusual.
            "amount_vs_account": (tx["amount"] / prior_mean).fillna(1.0),
            "prior_transactions": earlier.astype("int64"),
        }
    )
    write_deltalake(f"{lake}/{GOLD}", gold, mode="overwrite", schema_mode="overwrite")
    return len(gold)


def training_frame(lake: str, as_of: pd.Timestamp) -> pd.DataFrame:
    """Features joined to the verdicts that were known on a date.

    A model trained today cannot use a label that arrives next month, so the cutoff is the point
    of this function. Recent transactions drop out because nobody has judged them yet.
    """
    features = DeltaTable(f"{lake}/{GOLD}").to_pandas()
    labels = DeltaTable(f"{lake}/{LABELS}").to_pandas()
    known = labels[labels["labelled_at"] <= as_of][["transaction_id", "is_fraud"]]
    return features.merge(known, on="transaction_id", how="inner")
