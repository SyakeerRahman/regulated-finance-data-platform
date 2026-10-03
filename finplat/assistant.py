"""The language model's three jobs: explain one alert, investigate one alert, answer a question.

Two rules hold for all three:

1. The model reads and advises. It never decides. Its tools read data and none of them write,
   and its suggestion is stored beside the analyst's decision, never in it. A suggestion that
   became a label would train the next model on the language model's mistakes.
2. The model is optional. When it fails, the alert still has its SHAP reason and its policy rule,
   which are computed without it.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from finplat import policy, prompts
from finplat.alerts import CONFIDENCES, SUGGESTIONS, TOP_REASON, Store
from finplat.domain import HOME_COUNTRY
from finplat.llm import LLM, LLMError

# A tool answer longer than this is cut. A model reading 200 rows answers no better than one
# reading 20, and pays for every one.
TOOL_RESULT_CHARS = 6_000
MAX_TOOL_ROUNDS = 6
# Measured on 2026-10-03: one case note called the same tool with the same arguments 21 times
# and took 37 s. A repeat gets a short note instead of a second run, and the total is capped.
MAX_TOOL_CALLS = 12
SUMMARY_CHARS = 600

ALERT_FIELDS = (
    "alert_id",
    "transaction_id",
    "account_id",
    "amount",
    "country",
    "channel",
    "category",
    "occurred_at",
    "score",
    "threshold",
    "model_version",
    "status",
    "reason",
    "contributions",
    "policy_rules",
    "ai_suggestion",
)


def _plain(value):
    """A database row as JSON: times as text, numerics as floats."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def compact(alert: dict) -> dict:
    return {key: _plain(alert.get(key)) for key in ALERT_FIELDS if alert.get(key) is not None}


def rules_for(alert: dict) -> list[policy.Rule]:
    """The rules stored with the alert, or, for an alert raised before stage G, cited now from its fields."""
    if alert.get("policy_rules"):
        return [policy.BY_ID[rule_id] for rule_id in alert["policy_rules"] if rule_id in policy.BY_ID]
    facts = {**(alert.get("features") or {}), "country": alert["country"], "amount": float(alert["amount"])}
    return policy.cite(facts, alert.get("contributions") or {})


# --- explain one alert ---------------------------------------------------------------------------


def narration_input(alert: dict, earlier: list[dict]) -> dict:
    """Everything the model may use, and nothing else."""
    features = alert.get("features") or {}
    rule = rules_for(alert)[0]
    account = {"earlier_payments": features.get("prior_transactions")}
    if features.get("amount_vs_account"):
        account["times_average_spend"] = round(float(features["amount_vs_account"]), 2)
        account["average_spend"] = round(float(alert["amount"]) / float(features["amount_vs_account"]), 2)
    account["earlier_alerts"] = [
        {"amount": float(row["amount"]), "status": row["status"], "category": row["category"]}
        for row in earlier
        if row["alert_id"] != alert["alert_id"]
    ][:10]
    return {
        "alert": compact(alert),
        "features": features,
        "policy_rule": rule.public(),
        "account": account,
    }


def narrate(llm: LLM, alert: dict, earlier: list[dict]) -> dict:
    """Two sentences, a suggestion and a check, validated before anything stores them."""
    prompt = prompts.load("narrate")
    message = llm.chat(
        [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": json.dumps(narration_input(alert, earlier), default=str)},
        ],
        json_output=True,
        max_tokens=400,
    )
    return parse_narrative(message.get("content") or "")


def parse_narrative(content: str) -> dict:
    try:
        answer = json.loads(content)
    except json.JSONDecodeError as error:
        raise LLMError(f"the narrative is not JSON: {content[:200]}") from error
    if not isinstance(answer, dict):
        raise LLMError("the narrative is not a JSON object")

    summary = str(answer.get("summary") or "").strip()
    suggestion = str(answer.get("suggestion") or "").strip().lower()
    confidence = str(answer.get("confidence") or "").strip().lower()
    if not summary:
        raise LLMError("the narrative has no summary")
    if suggestion not in SUGGESTIONS:
        raise LLMError(f"the suggestion {suggestion!r} is not one of {SUGGESTIONS}")
    if confidence not in CONFIDENCES:
        confidence = "low"
    return {
        "summary": summary[:SUMMARY_CHARS],
        "suggestion": suggestion,
        "confidence": confidence,
        "check": str(answer.get("check") or "").strip()[:SUMMARY_CHARS] or None,
    }


# --- the tools -----------------------------------------------------------------------------------


def _tool(name: str, description: str, properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)},
        },
    }


TOOLS = [
    _tool(
        "search_alerts",
        "Find alerts, newest first. Every filter is optional.",
        {
            "status": {"type": "string", "enum": ["open", "confirmed_fraud", "false_positive"]},
            "country": {"type": "string", "description": "Two-letter merchant country, for example SG"},
            "abroad": {
                "type": "boolean",
                "description": "true: only merchants outside Malaysia. false: only merchants in Malaysia",
            },
            "reason": {
                "type": "string",
                "description": "The feature that raised the score most",
                "enum": [
                    "amount_vs_account",
                    "is_abroad",
                    "is_online",
                    "is_night",
                    "is_risky_category",
                    "prior_transactions",
                    "amount",
                    "hour",
                ],
            },
            "account_id": {"type": "string"},
            "min_amount": {"type": "number", "description": "MYR"},
            "hours": {"type": "integer", "description": "Only alerts raised in the last N hours"},
            "limit": {"type": "integer", "description": "At most 20"},
        },
    ),
    _tool(
        "get_alert",
        "One alert in full: payment, score, SHAP reasons, policy rules, status.",
        {"alert_id": {"type": "integer"}},
        ("alert_id",),
    ),
    _tool(
        "account_history",
        "One account: its payments in the lake, its average spend, and every alert raised against it.",
        {"account_id": {"type": "string"}, "limit": {"type": "integer", "description": "Payments to list, at most 30"}},
        ("account_id",),
    ),
    _tool(
        "alert_stats",
        "Alert counts by status and by top reason, for the last N hours.",
        {"hours": {"type": "integer", "description": "Default 24"}},
    ),
    _tool(
        "top_accounts",
        "The accounts with the most alerts in the last N hours.",
        {"hours": {"type": "integer"}, "limit": {"type": "integer"}},
    ),
    _tool(
        "policy_rules",
        "The fraud policy. Give a rule id for one rule, or nothing for all twelve.",
        {"rule_id": {"type": "string", "description": "For example FP-2"}},
    ),
    _tool("model_info", "The live fraud model: version, threshold, and the precision and recall of each version.", {}),
    _tool("ai_agreement", "How often the AI suggestion matched the analyst's decision.", {}),
]


@dataclass
class Toolbox:
    """Read-only access to the platform. Nothing here writes."""

    store: Store
    lake: str
    model_info: Callable[[], dict]

    def run(self, name: str, arguments: dict) -> object:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return {"error": f"there is no tool called {name}"}
        try:
            return handler(**arguments)
        except TypeError as error:
            # A model that sends a wrong argument gets told, and can try again. It does not crash the answer.
            return {"error": f"wrong arguments for {name}: {error}"}
        except Exception as error:  # noqa: BLE001 - a failed tool is an answer the model can read
            return {"error": f"{name} failed: {error}"}

    def tool_search_alerts(
        self,
        status=None,
        country=None,
        abroad=None,
        reason=None,
        account_id=None,
        min_amount=None,
        hours=None,
        limit=10,
    ) -> dict:
        clauses, params = [], {"limit": max(1, min(int(limit or 10), 20))}
        if status:
            clauses.append("status = %(status)s")
            params["status"] = status
        if country:
            clauses.append("country = %(country)s")
            params["country"] = country.upper()
        # Seen live: asked for alerts outside Malaysia, with no way to say "not MY", the model
        # dropped the filter and listed Malaysian alerts as the answer.
        if abroad is not None:
            clauses.append("country <> %(home)s" if abroad else "country = %(home)s")
            params["home"] = HOME_COUNTRY
        if reason:
            clauses.append(f"{TOP_REASON} = %(reason)s")
            params["reason"] = reason
        if account_id:
            clauses.append("account_id = %(account_id)s")
            params["account_id"] = account_id.upper()
        if min_amount is not None:
            clauses.append("amount >= %(min_amount)s")
            params["min_amount"] = float(min_amount)
        if hours:
            clauses.append("created_at >= %(since)s")
            params["since"] = datetime.now(UTC) - timedelta(hours=int(hours))
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self.store.connect() as connection:
            total = connection.execute(f"select count(*) as n from alerts {where}", params).fetchone()["n"]
            rows = connection.execute(
                f"""
                select alert_id, account_id, amount, country, category, channel, score, status, created_at,
                       policy_rules, features, contributions, {TOP_REASON} as top_reason
                from alerts {where} order by created_at desc limit %(limit)s
                """,
                params,
            ).fetchall()
        for row in rows:
            # Seen live: an alert from before stage G has no stored rule, and the model told the
            # analyst the rule was "not specified". Cite it here, as the dashboard does.
            # The title rides with the id. Given only "FP-12", the model described the rule from
            # its imagination instead of reading it.
            row["policy_rules"] = [f"{rule.rule_id} {rule.title}" for rule in rules_for(row)]
            del row["features"], row["contributions"]
        return {"total_matching": total, "alerts": _plain(rows)}

    def tool_get_alert(self, alert_id) -> dict:
        alert = self.store.get(int(alert_id))
        if alert is None:
            return {"error": f"there is no alert {alert_id}"}
        return {**compact(alert), "features": alert.get("features"), "rules": [r.public() for r in rules_for(alert)]}

    def tool_account_history(self, account_id, limit=15) -> dict:
        import pyarrow.compute as pc
        from deltalake import DeltaTable

        from finplat.pipeline import GOLD

        account_id = str(account_id).upper()
        table = (
            DeltaTable(f"{self.lake}/{GOLD}")
            .to_pyarrow_dataset()
            .to_table(
                filter=pc.field("account_id") == account_id,
                columns=[
                    "ts",
                    "amount",
                    "amount_vs_account",
                    "is_abroad",
                    "is_online",
                    "is_risky_category",
                    "is_night",
                ],
            )
        )
        frame = table.to_pandas().sort_values("ts", ascending=False)
        alerts = self.store.for_account(account_id, limit=20)
        if frame.empty and not alerts:
            return {"error": f"no payments and no alerts for {account_id}"}
        return {
            "account_id": account_id,
            "payments_in_lake": len(frame),
            "average_spend": round(float(frame["amount"].mean()), 2) if len(frame) else None,
            "largest_payment": round(float(frame["amount"].max()), 2) if len(frame) else None,
            "share_abroad": round(float(frame["is_abroad"].mean()), 3) if len(frame) else None,
            "share_online": round(float(frame["is_online"].mean()), 3) if len(frame) else None,
            "recent_payments": _plain(
                frame.head(max(1, min(int(limit or 15), 30)))
                .assign(
                    ts=lambda f: f["ts"].astype(str),
                    amount=lambda f: f["amount"].round(2),
                    amount_vs_account=lambda f: f["amount_vs_account"].round(2),
                )
                .to_dict("records")
            ),
            "alerts": [compact(row) for row in alerts],
        }

    def tool_alert_stats(self, hours=24) -> dict:
        since = datetime.now(UTC) - timedelta(hours=int(hours or 24))
        with self.store.connect() as connection:
            by_status = connection.execute(
                "select status, count(*) as n from alerts where created_at >= %s group by 1", (since,)
            ).fetchall()
            by_reason = connection.execute(
                f"select {TOP_REASON} as reason, count(*) as n from alerts where created_at >= %s "
                "group by 1 order by 2 desc",
                (since,),
            ).fetchall()
            by_rule = connection.execute(
                "select policy_rules[1] as rule, count(*) as n from alerts where created_at >= %s "
                "and policy_rules is not null group by 1 order by 2 desc",
                (since,),
            ).fetchall()
        return {"hours": int(hours or 24), "by_status": by_status, "by_reason": by_reason, "by_rule": by_rule}

    def tool_top_accounts(self, hours=168, limit=10) -> dict:
        since = datetime.now(UTC) - timedelta(hours=int(hours or 168))
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                select account_id, count(*) as alerts, sum(amount) as total_amount,
                       count(*) filter (where status = 'confirmed_fraud') as confirmed
                from alerts where created_at >= %s group by 1 order by 2 desc, 3 desc limit %s
                """,
                (since, max(1, min(int(limit or 10), 20))),
            ).fetchall()
        return {"hours": int(hours or 168), "accounts": _plain(rows)}

    def tool_policy_rules(self, rule_id=None) -> dict:
        if rule_id:
            rule = policy.BY_ID.get(str(rule_id).upper())
            return rule.public() if rule else {"error": f"there is no rule {rule_id}"}
        return {"policy": policy.POLICY_NAME, "rules": [rule.public() for rule in policy.RULES]}

    def tool_model_info(self) -> dict:
        return _plain(self.model_info())

    def tool_ai_agreement(self) -> dict:
        return self.store.agreement()


# --- the agent loop ------------------------------------------------------------------------------


def run_agent(
    llm: LLM, toolbox: Toolbox, system: str, messages: list[dict], on_event: Callable[[dict], None] | None = None
) -> dict:
    """Let the model call tools until it answers. The last round must answer, with no tools.

    With `on_event`, the answer streams: a `tool` event as each tool runs, a `delta` event for each
    piece of text, and `discard` when text already sent turns out to come before a tool call.
    """
    conversation = [{"role": "system", "content": system}, *messages]
    used = []
    seen: set[str] = set()
    empty = 0
    for round_number in range(MAX_TOOL_ROUNDS):
        last = round_number == MAX_TOOL_ROUNDS - 1
        options = {"tools": TOOLS, "tool_choice": "none" if last else None, "max_tokens": 900}
        streamed: list[str] = []
        if on_event:

            def send(text: str, streamed=streamed) -> None:
                streamed.append(text)
                on_event({"type": "delta", "text": text})

            reply = llm.stream(conversation, on_text=send, **options)
        else:
            reply = llm.chat(conversation, **options)
        calls = reply.get("tool_calls") or []
        if calls and streamed:
            # The same rule as below, for a reader who has already seen the guess.
            on_event({"type": "discard"})
        if not calls:
            answer = (reply.get("content") or "").strip()
            if answer:
                return {"answer": answer, "tools": used}
            # Seen live through OpenRouter: 2 of 3 replies to one question had no text and no
            # tool call. One retry, then a clear error. An empty answer must never look like one.
            empty += 1
            if empty > 1:
                raise LLMError(f"{llm.model} answered with nothing, twice")
            continue

        # The assistant turn that asked for the tools goes back in first. Without it, the tool
        # results answer a question the provider has no record of, and it refuses the request.
        # Its text is dropped: written before any tool ran, it is a guess, and it was seen
        # inventing a model version and an agreement rate.
        conversation.append({"role": "assistant", "content": "", "tool_calls": calls})
        for call in calls:
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = None
            key = f"{name}:{json.dumps(arguments, sort_keys=True)}"
            if not isinstance(arguments, dict):
                result = {"error": "the arguments were not a JSON object"}
            elif key in seen:
                result = {"note": "You already have this result, above. Use it and write the answer."}
            elif len(used) >= MAX_TOOL_CALLS:
                result = {"error": f"The limit of {MAX_TOOL_CALLS} tool calls is reached. Answer with what you have."}
            else:
                seen.add(key)
                if on_event:
                    on_event({"type": "tool", "tool": name})
                result = toolbox.run(name, arguments)
                used.append({"tool": name, "arguments": arguments})
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, default=str)[:TOOL_RESULT_CHARS],
                }
            )
    raise LLMError(f"no answer after {MAX_TOOL_ROUNDS} rounds of tool calls")


def ask(llm: LLM, toolbox: Toolbox, messages: list[dict], on_event: Callable[[dict], None] | None = None) -> dict:
    prompt = prompts.load("ask")
    answer = run_agent(llm, toolbox, prompt.text, messages, on_event)
    return {**answer, "prompt_version": prompt.version, "model": llm.model}


def case_note(llm: LLM, toolbox: Toolbox, alert_id: int) -> dict:
    prompt = prompts.load("case_note")
    answer = run_agent(
        llm, toolbox, prompt.text, [{"role": "user", "content": f"Write the case note for alert #{alert_id}."}]
    )
    if not answer["answer"]:
        raise LLMError("the case note is empty")
    return {**answer, "prompt_version": prompt.version, "model": llm.model}
