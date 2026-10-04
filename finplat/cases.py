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
import re
from datetime import datetime, timedelta

from finplat import policy
from finplat.alerts import CONFIRMED, DECISIONS, FALSE_POSITIVE, TOP_REASON, Store
from finplat.embed import Embedder

ANALYST = "analyst"
LABEL = "label"
# The true answer, rebuilt from the generator for a demo. It stands for an analyst team that
# decides each case within DECIDED_AFTER of the alert. It lives only here and never in
# `alerts.status`, so it cannot become a training label: `export_decisions` reads only the analyst
# status.
SIMULATED = "simulated"
DECIDED_AFTER = timedelta(hours=1)

# Texts in one embedding request. Each request is one call from the daily budget.
BATCH = 64


def query_text(alert: dict) -> str:
    """What an alert is before anyone wrote about it: the payment, why it was flagged, the rules."""
    rules = [policy.BY_ID[rule_id] for rule_id in alert.get("policy_rules") or [] if rule_id in policy.BY_ID]
    lines = [
        f"{alert['category']} payment of MYR {float(alert['amount']):.2f}, {alert['channel']}, in {alert['country']}.",
        f"Top reason: {alert.get('top_reason') or alert['reason']}.",
    ]
    if rules:
        lines.append("Policy: " + "; ".join(f"{rule.rule_id} {rule.title}" for rule in rules) + ".")
    return "\n".join(lines)


def case_text(alert: dict) -> str:
    """The words a case is found by: the alert, and what was written about it."""
    lines = [query_text(alert), f"Summary: {alert['ai_summary']}"]
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


def evidence(row: dict) -> dict:
    """One past case as the model and the dashboard see it. No outcome yet reads as pending."""
    return {
        "alert_id": row["alert_id"],
        "amount": round(float(row["amount"]), 2),
        "category": row["category"],
        "country": row["country"],
        "channel": row["channel"],
        "score": round(float(row["score"]), 4),
        "outcome": row["outcome"] or "pending",
        "outcome_source": row["outcome_source"],
        "distance": None if row.get("distance") is None else round(float(row["distance"]), 3),
    }


def past_cases(store: Store, embedder: Embedder, alert: dict, limit: int = 5) -> list[dict]:
    """The nearest earlier cases for one alert, as they were known when it was raised.

    An alert that is a case already is searched with its stored vector, which costs nothing.
    Otherwise its text is embedded: one call from the budget.
    """
    with store.connect() as connection:
        stored = connection.execute(
            "select embedding::text as vector from case_vectors where alert_id = %s and embed_model = %s",
            (alert["alert_id"], embedder.model),
        ).fetchone()
    if stored:
        vector = [float(value) for value in stored["vector"].strip("[]").split(",")]
    else:
        vector = embedder.embed([query_text(alert)])[0]
    rows = search(store, vector, embedder.model, alert["created_at"], exclude=alert["alert_id"], limit=limit)
    return [evidence(row) for row in rows]


def cited(text: str, cases: list[dict]) -> list[int]:
    """The past cases a note names by id, such as #567, in the order given. Others are ignored."""
    named = {int(match) for match in re.findall(r"#(\d+)\b", text or "")}
    return [case["alert_id"] for case in cases if case["alert_id"] in named]


def by_ids(store: Store, alert_ids: list[int], as_of: datetime) -> list[dict]:
    """Cited cases, in the order given, with their outcomes as known at `as_of`."""
    if not alert_ids:
        return []
    with store.connect() as connection:
        rows = connection.execute(
            """
            select a.alert_id, a.amount, a.category, a.country, a.channel, a.score,
                   case when c.known_at <= %(as_of)s then c.outcome end as outcome,
                   case when c.known_at <= %(as_of)s then c.outcome_source end as outcome_source
            from alerts a left join case_vectors c using (alert_id)
            where a.alert_id = any(%(ids)s::bigint[])
            """,
            {"ids": list(alert_ids), "as_of": as_of},
        ).fetchall()
    found = {row["alert_id"]: evidence(row) for row in rows}
    return [found[alert_id] for alert_id in alert_ids if alert_id in found]


def simulate(store: Store, seed: int, accounts: int, count: int) -> int:
    """Give up to `count` cases with no real outcome their true answer. For a demo only.

    Each is known DECIDED_AFTER its alert, as if a team had decided it then. An older alert can
    then see the outcome of a case before it, which a grading of retrieval needs. A case with an
    analyst decision or a label is never touched, and a second run rewrites the same answers.
    """
    from finplat.ai_eval import truth_for

    with store.connect() as connection:
        open_cases = connection.execute(
            """
            select a.alert_id, a.transaction_id, a.amount, a.created_at
            from case_vectors c join alerts a using (alert_id)
            where c.outcome is null or c.outcome_source = %s order by a.alert_id limit %s
            """,
            (SIMULATED, count),
        ).fetchall()
    truth = truth_for(open_cases, seed, accounts)
    with store.connect() as connection:
        for alert in open_cases:
            if alert["transaction_id"] in truth:
                verdict = CONFIRMED if truth[alert["transaction_id"]] else FALSE_POSITIVE
                connection.execute(
                    """
                    update case_vectors set outcome = %s, outcome_source = %s, known_at = %s
                    where alert_id = %s and (outcome is null or outcome_source = %s)
                    """,
                    (verdict, SIMULATED, alert["created_at"] + DECIDED_AFTER, alert["alert_id"], SIMULATED),
                )
    return sum(alert["transaction_id"] in truth for alert in open_cases)


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
