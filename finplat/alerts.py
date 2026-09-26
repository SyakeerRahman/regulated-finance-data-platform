"""Alerts and the decisions an analyst makes about them.

Redis and Delta are the wrong homes for this. An alert has to survive a restart, be fetched by
its own id, and change state when somebody presses a button. That is a row in a database.

A decision is the same kind of fact as a chargeback, so `export_decisions` writes it back to
`silver/labels` with `label_source = analyst`. Next week's retrain reads it. That is the loop
closing, and it is how a real fraud team feeds its own model.
"""

import json
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

OPEN = "open"
CONFIRMED = "confirmed_fraud"
FALSE_POSITIVE = "false_positive"
DECISIONS = (CONFIRMED, FALSE_POSITIVE)

SCHEMA = """
create table if not exists alerts (
    alert_id        bigserial primary key,
    transaction_id  text not null unique,
    account_id      text not null,
    amount          double precision not null,
    country         text not null,
    channel         text not null,
    category        text not null,
    occurred_at     timestamptz not null,
    score           double precision not null,
    threshold       double precision not null,
    model_version   text,
    reason          text not null,
    contributions   jsonb not null,
    status          text not null default 'open',
    decided_at      timestamptz,
    created_at      timestamptz not null default now()
);
create index if not exists alerts_status_idx on alerts (status, created_at desc);
create index if not exists alerts_account_idx on alerts (account_id);
"""


@dataclass
class Store:
    dsn: str

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute(SCHEMA)

    def raise_alert(self, result: dict, reason: str, contributions: dict) -> int | None:
        """Store one alert. A repeated transaction_id is ignored, so a replay adds nothing."""
        with self.connect() as connection:
            row = connection.execute(
                """
                insert into alerts (transaction_id, account_id, amount, country, channel, category,
                                    occurred_at, score, threshold, model_version, reason, contributions)
                values (%(transaction_id)s, %(account_id)s, %(amount)s, %(country)s, %(channel)s,
                        %(category)s, %(occurred_at)s, %(score)s, %(threshold)s, %(model_version)s,
                        %(reason)s, %(contributions)s)
                on conflict (transaction_id) do nothing
                returning alert_id
                """,
                {
                    "transaction_id": result["transaction_id"],
                    "account_id": result["account_id"],
                    "amount": result["amount"],
                    "country": result["country"],
                    "channel": result["channel"],
                    "category": result["merchant_category"],
                    "occurred_at": result["ts"],
                    "score": result["score"],
                    "threshold": result["threshold"],
                    "model_version": result["model_version"],
                    "reason": reason,
                    "contributions": json.dumps(contributions),
                },
            ).fetchone()
            return row["alert_id"] if row else None

    def recent(self, limit: int = 50, status: str | None = None) -> list[dict]:
        clause = "where status = %(status)s" if status else ""
        with self.connect() as connection:
            return connection.execute(
                f"select * from alerts {clause} order by created_at desc limit %(limit)s",
                {"limit": limit, "status": status},
            ).fetchall()

    def decide(self, alert_id: int, status: str) -> dict | None:
        if status not in DECISIONS:
            raise ValueError(f"status must be one of {DECISIONS}")
        with self.connect() as connection:
            return connection.execute(
                "update alerts set status = %s, decided_at = now() where alert_id = %s returning *",
                (status, alert_id),
            ).fetchone()

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("select status, count(*) as n from alerts group by status").fetchall()
        return {row["status"]: row["n"] for row in rows}

    def decisions(self) -> list[dict]:
        """Every decision an analyst has made, for the write-back to the label table."""
        with self.connect() as connection:
            return connection.execute(
                "select transaction_id, status, decided_at from alerts where status <> %s order by decided_at",
                (OPEN,),
            ).fetchall()


def export_decisions(store: Store, lake: str) -> int:
    """Write analyst decisions into `silver/labels`, so the next retrain reads them.

    A chargeback and an analyst verdict are the same kind of fact, so they share one table and
    differ only in `label_source`.
    """
    import pandas as pd

    from finplat.pipeline import LABELS, replace_batch

    rows = store.decisions()
    if not rows:
        return 0

    frame = pd.DataFrame(
        [
            {
                "transaction_id": row["transaction_id"],
                "is_fraud": row["status"] == CONFIRMED,
                "label_source": "analyst",
                "labelled_at": pd.Timestamp(row["decided_at"]).tz_convert("UTC"),
            }
            for row in rows
        ]
    )
    # One partition for every analyst decision, replaced whole each time. An analyst can change
    # their mind, and the label table must then hold the new verdict and not both.
    replace_batch(f"{lake}/{LABELS}", frame.assign(batch_id="analyst"), "analyst")
    return len(frame)
