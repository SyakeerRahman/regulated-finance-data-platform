"""What each lake table holds, and what each of its columns means.

One definition, read by the Data tab, the AI tools and the tests. `tests/test_catalog.py` compares
it with the real Delta schemas, so a new column without a description fails CI.
"""

from dataclasses import dataclass

from finplat.domain import ABROAD, CATEGORIES, CHANNELS, HOME_COUNTRY, RISKY_CATEGORIES
from finplat.pipeline import BRONZE, GOLD, LABELS, QUARANTINE, SILVER
from finplat.quality import QUALITY
from finplat.retention import RETENTION_DAYS


@dataclass(frozen=True)
class Table:
    purpose: str
    grain: str
    written_by: str
    rerun: str
    columns: dict[str, str]


# The columns every layer keeps from the payment as it arrived.
PAYMENT = {
    "transaction_id": "Unique id of the payment, for example 20261003-0000123. A later live feed round gives ids such as 20261003-r2-0000123.",
    "account_id": "Card account that paid, for example ACC00042. Never null after silver.",
    "merchant_category": f"Merchant type. One of {', '.join(CATEGORIES)}.",
    "amount": "Payment amount in MYR. Always above 0 after silver.",
    "currency": "ISO 4217 currency code. Always MYR in this feed.",
    "country": f"ISO country code of the merchant. {HOME_COUNTRY} is home; abroad is one of {', '.join(ABROAD)}.",
    "channel": f"How the card was used. One of {', '.join(CHANNELS)}.",
    "ts": "When the payment happened, in UTC.",
}
BATCH_ID = "The batch that delivered the row: a day such as 2026-10-03, or live-<day> for the live feed."

CATALOG = {
    BRONZE: Table(
        purpose="Every payment exactly as received. Nothing is fixed, so an auditor can see the original.",
        grain="One row per payment received. A repeated payment appears more than once.",
        written_by="pipeline.load_bronze, and pipeline.append_bronze for the live feed",
        rerun="The batch partition is overwritten with the predicate batch_id = '<id>'.",
        columns={
            **PAYMENT,
            "batch_id": BATCH_ID,
            "ingested_at": "When the platform received the batch, in UTC. The freshness check reads it.",
        },
    ),
    QUARANTINE: Table(
        purpose="Payments that failed validation, with the reason. They never reach silver or gold.",
        grain="One row per rejected payment.",
        written_by="pipeline.refine_silver",
        rerun="The batch partition is overwritten with the predicate batch_id = '<id>'.",
        columns={
            **PAYMENT,
            "account_id": "Card account that paid. Null when the reason is missing account.",
            "amount": "Payment amount in MYR. 0 or below when the reason is non-positive amount.",
            "batch_id": BATCH_ID,
            "reason": "Why the row was rejected: missing account, or non-positive amount.",
        },
    ),
    SILVER: Table(
        purpose="Clean payments. Valid rows only, with repeats removed.",
        grain="One row per transaction_id.",
        written_by="pipeline.refine_silver",
        rerun="Delta MERGE on transaction_id, after drop_duplicates inside the batch.",
        columns={**PAYMENT, "batch_id": BATCH_ID},
    ),
    LABELS: Table(
        purpose="The fraud verdict on each payment, dated when a bank would learn it.",
        grain="One row per verdict. A payment can have a dated label and an analyst label.",
        written_by="pipeline.load_labels, and alerts.export_decisions for analyst decisions",
        rerun="The batch partition is overwritten. The analyst partition is rewritten whole each time.",
        columns={
            "transaction_id": "The payment the verdict is about. Joins to silver and gold.",
            "is_fraud": "True when the payment was fraud.",
            "label_source": "Where the verdict came from: chargeback, dispute window closed, or analyst.",
            "labelled_at": "When the verdict became known, in UTC. Training reads only labels known by its cutoff.",
            "batch_id": "The batch of the payment, or analyst for analyst decisions. The analyst batch never expires.",
        },
    ),
    GOLD: Table(
        purpose="Model features for every silver payment. It has no label column.",
        grain="One row per transaction_id in silver.",
        written_by="pipeline.build_gold",
        rerun="Full rebuild from all of silver, mode overwrite.",
        columns={
            "transaction_id": PAYMENT["transaction_id"],
            "account_id": PAYMENT["account_id"],
            "ts": PAYMENT["ts"],
            "amount": "Payment amount in MYR. A model feature.",
            "hour": "Hour of ts in UTC, 0 to 23.",
            "is_night": "True when hour is below 6.",
            "is_abroad": f"True when country is not {HOME_COUNTRY}.",
            "is_online": "True when channel is online.",
            "is_risky_category": f"True when merchant_category is one of {', '.join(sorted(RISKY_CATEGORIES))}.",
            "amount_vs_account": (
                "Amount divided by the mean amount of the account's earlier payments only. "
                "1.0 for the account's first payment. A mean that includes this row or later rows leaks the future."
            ),
            "prior_transactions": "How many payments the account made before this one.",
        },
    ),
    QUALITY: Table(
        purpose="The result of each data quality check on each batch. A failed critical check stops the run.",
        grain="One row per check per batch.",
        written_by="quality.record",
        rerun="The batch partition is overwritten with the predicate batch_id = '<id>'.",
        columns={
            "batch_id": "The batch that was checked.",
            "checked_at": "When the checks ran, in UTC.",
            "check": "The check: schema, freshness, volume, quarantine rate, duplicate rate, or amount median.",
            "severity": "critical stops the run before gold. warning is recorded and passes.",
            "passed": "True when the value is inside the threshold.",
            "value": "What the check measured, for example the quarantine rate.",
            "threshold": "The limit the value is compared with.",
            "detail": "The result in words, for a person to read.",
        },
    ),
}


def retention(table: str) -> str:
    """How long the table keeps a batch, from the same numbers `retention.drop_expired` uses."""
    if table == GOLD:
        return f"Follows silver: {RETENTION_DAYS[SILVER]} days."
    return f"{RETENTION_DAYS[table]} days."


def describe(table: str) -> dict:
    """The full entry for one table, as plain data."""
    entry = CATALOG[table]
    return {
        "table": table,
        "purpose": entry.purpose,
        "grain": entry.grain,
        "written_by": entry.written_by,
        "rerun": entry.rerun,
        "retention": retention(table),
        "columns": entry.columns,
    }
