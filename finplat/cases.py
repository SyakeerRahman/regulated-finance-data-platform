"""Past cases as vectors, for the case search.

A case is one alert with an AI summary. Its text is what an analyst reads on the card, and its
vector is the embedding of that text. Beside the vector sit the outcome and the time it became
known, so a search can show an alert only what was known when it was raised. A feature that sees
the future scores well in a demo and fails in production; see `amount_vs_account`.

Run `python -m finplat.cases index` to index every case, and again at any time: a case whose text
has not changed costs nothing. `python -m finplat.cases simulate` gives pending cases their true
answer, for a demo, without touching the alerts themselves.
"""

import argparse
import json
from datetime import UTC, datetime

from finplat import policy
from finplat.alerts import CONFIRMED, DECISIONS, FALSE_POSITIVE, TOP_REASON, Store
from finplat.embed import Embedder

ANALYST = "analyst"
LABEL = "label"
# The true answer, rebuilt from the generator for a demo. It stands for an analyst who decided the
# case when the command ran. It lives only here and never in `alerts.status`, so it cannot become
# a training label: `export_decisions` reads only the analyst status.
SIMULATED = "simulated"

# Texts in one embedding request. Each request is one call from the daily budget.
BATCH = 64


def case_text(alert: dict) -> str:
    """The words a case is found by: the payment, why it was flagged, and what was written about it."""
    rules = [policy.BY_ID[rule_id] for rule_id in alert.get("policy_rules") or [] if rule_id in policy.BY_ID]
    lines = [
        f"{alert['category']} payment of MYR {float(alert['amount']):.2f}, {alert['channel']}, in {alert['country']}.",
        f"Top reason: {alert.get('top_reason') or alert['reason']}.",
    ]
    if rules:
        lines.append("Policy: " + "; ".join(f"{rule.rule_id} {rule.title}" for rule in rules) + ".")
    lines.append(f"Summary: {alert['ai_summary']}")
    if alert.get("case_note"):
        lines.append(f"Case note: {alert['case_note']}")
    return "\n".join(lines)


def _vector(values: list[float]) -> str:
    """pgvector's text form. A literal, so no client library is needed for one column type."""
    return "[" + ",".join(repr(float(value)) for value in values) + "]"


def known_labels(lake: str, transaction_ids: list[str]) -> dict[str, tuple[str, datetime]]:
    """The label of each payment, and when it is known. It may be known only in the future.

    Analyst rows are left out: the alert row already holds the decision, with its own time.
    """
    if not transaction_ids:
        return {}
    from deltalake import DeltaTable

    from finplat.pipeline import LABELS

    try:
        labels = DeltaTable(f"{lake}/{LABELS}").to_pandas(
            columns=["transaction_id", "is_fraud", "label_source", "labelled_at"]
        )
    except Exception:  # noqa: BLE001 - no label table yet is the same as no labels
        return {}
    wanted = labels[labels["transaction_id"].isin(set(transaction_ids)) & (labels["label_source"] != ANALYST)]
    return {
        row.transaction_id: (CONFIRMED if row.is_fraud else FALSE_POSITIVE, row.labelled_at.to_pydatetime())
        for row in wanted.itertuples()
    }


def outcome(alert: dict, labels: dict[str, tuple[str, datetime]]) -> tuple[str, str, datetime] | None:
    """(outcome, source, known_at). An analyst decision wins over a label."""
    if alert["status"] in DECISIONS:
        return alert["status"], ANALYST, alert["decided_at"]
    if alert["transaction_id"] in labels:
        verdict, known_at = labels[alert["transaction_id"]]
        return verdict, LABEL, known_at
    return None


def index(store: Store, embedder: Embedder, lake: str, alert_ids: list[int] | None = None) -> dict:
    """Embed every case that is new or has new text, then set every outcome. Safe to run twice."""
    with store.connect() as connection:
        alerts = connection.execute(
            f"""
            select a.*, {TOP_REASON} as top_reason, c.text as indexed_text, c.embed_model as indexed_model
            from alerts a left join case_vectors c using (alert_id)
            where a.ai_summary is not null and (%(all)s or a.alert_id = any(%(ids)s::bigint[]))
            order by a.alert_id
            """,
            {"all": alert_ids is None, "ids": list(alert_ids or [])},
        ).fetchall()

    stale = [
        (alert, text)
        for alert in alerts
        if (text := case_text(alert)) != alert["indexed_text"] or alert["indexed_model"] != embedder.model
    ]
    for start in range(0, len(stale), BATCH):
        chunk = stale[start : start + BATCH]
        vectors = embedder.embed([text for _, text in chunk])
        with store.connect() as connection:
            for (alert, text), vector in zip(chunk, vectors, strict=True):
                connection.execute(
                    """
                    insert into case_vectors (alert_id, text, embed_model, embedding)
                    values (%s, %s, %s, %s::vector)
                    on conflict (alert_id) do update set text = excluded.text,
                        embed_model = excluded.embed_model, embedding = excluded.embedding, indexed_at = now()
                    """,
                    (alert["alert_id"], text, embedder.model, _vector(vector)),
                )

    labels = known_labels(lake, [alert["transaction_id"] for alert in alerts])
    with store.connect() as connection:
        for alert in alerts:
            found = outcome(alert, labels)
            if found:
                connection.execute(
                    "update case_vectors set outcome = %s, outcome_source = %s, known_at = %s where alert_id = %s",
                    (*found, alert["alert_id"]),
                )
            else:
                # A decision can be taken back to open. A simulated outcome stays, because nothing
                # real has replaced it.
                connection.execute(
                    """
                    update case_vectors set outcome = null, outcome_source = null, known_at = null
                    where alert_id = %s and outcome_source is distinct from %s
                    """,
                    (alert["alert_id"], SIMULATED),
                )
    return {"cases": len(alerts), "embedded": len(stale), "requests": -(-len(stale) // BATCH)}


def search(
    store: Store, vector: list[float], model: str, as_of: datetime, exclude: int | None = None, limit: int = 5
) -> list[dict]:
    """The nearest past cases, as they looked at `as_of`.

    A case raised after `as_of` is not returned. An outcome known after `as_of` is returned as
    pending: the case existed, its verdict did not yet. Exact search, over every case.
    """
    with store.connect() as connection:
        return connection.execute(
            """
            select a.alert_id, a.transaction_id, a.amount, a.category, a.country, a.channel, a.score,
                   a.created_at, c.embedding <=> %(vector)s::vector as distance,
                   case when c.known_at <= %(as_of)s then c.outcome end as outcome,
                   case when c.known_at <= %(as_of)s then c.outcome_source end as outcome_source
            from case_vectors c join alerts a using (alert_id)
            where c.embed_model = %(model)s and a.created_at < %(as_of)s
              and a.alert_id is distinct from %(exclude)s
            order by distance
            limit %(limit)s
            """,
            {"vector": _vector(vector), "model": model, "as_of": as_of, "exclude": exclude, "limit": limit},
        ).fetchall()


def simulate(store: Store, seed: int, accounts: int, count: int, now: datetime | None = None) -> int:
    """Give up to `count` pending cases their true answer, known from `now`. For a demo only."""
    from finplat.ai_eval import truth_for

    with store.connect() as connection:
        pending = connection.execute(
            """
            select a.alert_id, a.transaction_id, a.amount from case_vectors c join alerts a using (alert_id)
            where c.outcome is null order by a.alert_id limit %s
            """,
            (count,),
        ).fetchall()
    truth = truth_for(pending, seed, accounts)
    known_at = now or datetime.now(UTC)
    with store.connect() as connection:
        for alert in pending:
            if alert["transaction_id"] in truth:
                verdict = CONFIRMED if truth[alert["transaction_id"]] else FALSE_POSITIVE
                connection.execute(
                    """
                    update case_vectors set outcome = %s, outcome_source = %s, known_at = %s
                    where alert_id = %s and outcome is null
                    """,
                    (verdict, SIMULATED, known_at, alert["alert_id"]),
                )
    return sum(alert["transaction_id"] in truth for alert in pending)


def main() -> None:
    from finplat.embed import from_settings
    from finplat.llm import Budget
    from finplat.settings import get_settings

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("index", help="embed new cases and set every outcome")
    simulated = commands.add_parser("simulate", help="give pending cases their true answer, for a demo")
    simulated.add_argument("--count", type=int, default=200)
    arguments = parser.parse_args()

    settings = get_settings()
    store = Store(settings.postgres_dsn)
    store.migrate()
    if arguments.command == "index":
        result = index(store, from_settings(settings, Budget(settings.llm_daily_calls)), settings.lake_uri)
    else:
        result = {"simulated": simulate(store, settings.seed, settings.accounts, arguments.count)}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
