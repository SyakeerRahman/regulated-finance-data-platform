"""Alerts and the decisions an analyst makes about them.

Redis and Delta are the wrong homes for this. An alert has to survive a restart, be fetched by
its own id, and change state when somebody presses a button. That is a row in a database.

A decision is the same kind of fact as a chargeback, so `export_decisions` writes it back to
`silver/labels` with `label_source = analyst`. Next week's retrain reads it. That is the loop
closing, and it is how a real fraud team feeds its own model.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row

OPEN = "open"
CONFIRMED = "confirmed_fraud"
FALSE_POSITIVE = "false_positive"
DECISIONS = (CONFIRMED, FALSE_POSITIVE)

# The feature that pushed the score up the most. SHAP gives every feature a contribution, and the
# largest positive one is the reason an analyst files the alert under.
TOP_REASON = "(select key from jsonb_each_text(contributions) order by value::float desc limit 1)"

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

    def search(
        self,
        *,
        since: datetime | None = None,
        status: str | None = None,
        country: str | None = None,
        reason: str | None = None,
        text: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """One page of alerts, newest first, and how many match in total."""
        clauses, params = [], {"limit": limit, "offset": offset}
        if since is not None:
            clauses.append("created_at >= %(since)s")
            params["since"] = since
        if status:
            clauses.append("status = %(status)s")
            params["status"] = status
        if country:
            clauses.append("country = %(country)s")
            params["country"] = country
        if reason:
            clauses.append(f"{TOP_REASON} = %(reason)s")
            params["reason"] = reason
        if text:
            clauses.append("(transaction_id ilike %(text)s or account_id ilike %(text)s or category ilike %(text)s)")
            params["text"] = f"%{text.strip()}%"
        where = f"where {' and '.join(clauses)}" if clauses else ""

        with self.connect() as connection:
            total = connection.execute(f"select count(*) as n from alerts {where}", params).fetchone()["n"]
            rows = connection.execute(
                f"""
                select *, {TOP_REASON} as top_reason from alerts {where}
                order by created_at desc, alert_id desc limit %(limit)s offset %(offset)s
                """,
                params,
            ).fetchall()
        return rows, total

    def summary(self, now: datetime) -> dict:
        """The last 24 hours against the 24 before, an hourly count by status, and the reasons."""
        day = timedelta(hours=24)
        params = {"start": now - day, "before": now - 2 * day, "now": now}
        with self.connect() as connection:
            windows = connection.execute(
                """
                select case when created_at >= %(start)s then 'current' else 'previous' end as window,
                       status, count(*) as n
                from alerts where created_at >= %(before)s and created_at <= %(now)s
                group by 1, 2
                """,
                params,
            ).fetchall()
            hourly = connection.execute(
                """
                select date_trunc('hour', created_at) as hour, status, count(*) as n
                from alerts where created_at >= %(start)s and created_at <= %(now)s
                group by 1, 2 order by 1
                """,
                params,
            ).fetchall()
            reasons = connection.execute(
                f"""
                select {TOP_REASON} as reason, count(*) as n
                from alerts where created_at >= %(start)s and created_at <= %(now)s
                group by 1 order by 2 desc
                """,
                params,
            ).fetchall()
            # The day before exists only if the store is older than it. An empty previous day
            # would show every count as new growth.
            oldest = connection.execute("select min(created_at) as at from alerts").fetchone()["at"]

        totals = {"current": {}, "previous": {}}
        for row in windows:
            totals[row["window"]][row["status"]] = row["n"]
        return {
            "current": totals["current"],
            "previous": totals["previous"] if oldest is not None and oldest <= now - 2 * day else None,
            "hourly": [{"hour": row["hour"].isoformat(), "status": row["status"], "n": row["n"]} for row in hourly],
            "reasons": [[row["reason"], row["n"]] for row in reasons if row["reason"]],
        }

    def reason_weights(self, limit: int = 500) -> list[list]:
        """The mean SHAP push of each feature across the latest alerts, largest first.

        Each alert keeps its three largest contributions, so a feature that is never in anyone's
        top three is absent here. That is the local view: what drives the alerts being raised
        now, next to the global view of what the trees lean on overall.
        """
        with self.connect() as connection:
            rows = connection.execute(
                """
                select key as feature, avg(value::float) as push, count(*) as alerts
                from (select contributions from alerts order by created_at desc limit %s) latest,
                     jsonb_each_text(latest.contributions)
                group by key order by 2 desc
                """,
                (limit,),
            ).fetchall()
        return [[row["feature"], row["push"], row["alerts"]] for row in rows]

    def decide_many(self, alert_ids: list[int], status: str) -> int:
        """The same decision on several alerts, in one transaction."""
        if status not in DECISIONS:
            raise ValueError(f"status must be one of {DECISIONS}")
        with self.connect() as connection:
            changed = connection.execute(
                "update alerts set status = %s, decided_at = now() where alert_id = any(%s)",
                (status, list(alert_ids)),
            )
            return changed.rowcount

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
