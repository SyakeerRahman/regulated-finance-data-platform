"""The AI layer: the policy citation, the prompt versions, the narrative, and the agent loop.

No test calls a real model. A fake one answers from a script, so the suite runs offline, costs
nothing, and gives the same result every time.
"""

import json
from datetime import date

import pytest
from deltalake import DeltaTable

from finplat import assistant, policy, prompts
from finplat.alerts import CONFIRMED, FALSE_POSITIVE, LIKELY_FALSE_POSITIVE, LIKELY_FRAUD, UNSURE, export_decisions
from finplat.llm import LLM, Budget, BudgetExceeded, LLMError
from finplat.pipeline import LABELS
from tests.test_alerts import result, store, test_dsn  # noqa: F401 - pytest finds fixtures by name


class FakeLLM(LLM):
    """Answers with the next scripted reply, and keeps every request it was sent."""

    def __init__(self, replies: list[dict]) -> None:
        super().__init__("http://fake", "fake-model", "key")
        self.replies = list(replies)
        self.requests: list[list[dict]] = []
        self.options: list[dict] = []

    def chat(self, messages, **options) -> dict:
        self.requests.append([dict(message) for message in messages])
        self.options.append(options)
        return self.replies.pop(0)

    def stream(self, messages, *, on_text, **options) -> dict:
        """The same reply, its text sent one word at a time, as a provider sends it in pieces."""
        reply = self.chat(messages, **options)
        for word in (reply.get("content") or "").split():
            on_text(word + " ")
        return reply


def tool_call(name: str, arguments: dict, call_id: str = "c1") -> dict:
    return {
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        ],
    }


SPIKE_ABROAD = {
    "amount": 153.14,
    "is_abroad": True,
    "is_online": False,
    "is_night": False,
    "is_risky_category": True,
    "amount_vs_account": 18.0,
    "prior_transactions": 40,
    "country": "SG",
}


# --- policy --------------------------------------------------------------------------------------


def test_every_rule_has_its_own_id_and_the_policy_has_twelve():
    assert len(policy.RULES) == 12
    assert len(policy.BY_ID) == 12


def test_the_cited_rule_is_the_one_the_shap_reasons_point_at():
    """18x spend abroad, with the spend pushing hardest: the spike-abroad rule, not the plain spike."""
    cited = policy.cite(SPIKE_ABROAD, {"amount_vs_account": 4.36, "is_abroad": 2.36, "is_risky_category": 1.56})

    assert cited[0].rule_id == "FP-2"
    assert {"FP-1", "FP-9", "FP-11"} <= {rule.rule_id for rule in cited}
    assert policy.FALLBACK not in cited


def test_a_feature_that_lowered_the_score_does_not_win_the_citation():
    cited = policy.cite(SPIKE_ABROAD, {"amount_vs_account": 4.0, "is_abroad": -3.0, "is_risky_category": 2.0})

    # FP-2 carries is_abroad, which pulled the score down. It still applies, but it is not first.
    assert cited[0].rule_id == "FP-11"


def test_a_payment_no_rule_names_falls_back_to_the_model_referral():
    quiet = {
        "amount": 40.0,
        "is_abroad": False,
        "is_online": False,
        "is_night": False,
        "is_risky_category": False,
        "amount_vs_account": 1.1,
        "prior_transactions": 30,
        "country": "MY",
    }
    assert policy.cite(quiet, {"amount": 0.2}) == [policy.FALLBACK]


def test_a_missing_fact_never_makes_a_rule_apply():
    """An alert from before stage G has no stored features. It must not cite a spike it cannot see."""
    cited = policy.cite({"country": "MY", "amount": 60.0}, {"amount_vs_account": 3.0})
    assert cited == [policy.FALLBACK]


def test_the_citation_is_the_same_on_every_run():
    contributions = {"amount_vs_account": 1.0, "is_abroad": 1.0}
    assert policy.cite(SPIKE_ABROAD, contributions) == policy.cite(SPIKE_ABROAD, contributions)


# --- prompts -------------------------------------------------------------------------------------


def test_a_prompt_version_changes_when_its_text_changes():
    assert prompts.version_of("Answer in JSON.") == prompts.version_of("Answer in JSON.")
    assert prompts.version_of("Answer in JSON.") != prompts.version_of("Answer in JSON. ")


def test_every_prompt_file_loads_with_a_version():
    versions = prompts.versions()
    assert set(versions) == {"ask", "case_note", "narrate"}
    assert all(len(version) == 8 for version in versions.values())


# --- the narrative -------------------------------------------------------------------------------


def alert_row(**overrides) -> dict:
    row = {
        "alert_id": 8,
        "transaction_id": "t-8",
        "account_id": "ACC39002",
        "amount": 153.14,
        "country": "SG",
        "channel": "chip",
        "category": "electronics",
        "status": "open",
        "score": 1.0,
        "threshold": 0.902,
        "contributions": {"amount_vs_account": 4.36, "is_abroad": 2.36},
        "features": SPIKE_ABROAD,
        "policy_rules": ["FP-2", "FP-1"],
    }
    return {**row, **overrides}


def test_the_narrative_is_parsed_and_checked():
    reply = {
        "summary": "18x normal spend abroad. FP-2 says hold the card.",
        "suggestion": "Likely_Fraud",
        "confidence": "high",
        "check": "Is the cardholder in Singapore?",
    }
    llm = FakeLLM([{"content": json.dumps(reply)}])

    narrative = assistant.narrate(llm, alert_row(), earlier=[])

    assert narrative["suggestion"] == LIKELY_FRAUD
    assert narrative["confidence"] == "high"
    sent = json.loads(llm.requests[0][1]["content"])
    # The model is given the rule. It does not choose one.
    assert sent["policy_rule"]["rule_id"] == "FP-2"
    assert sent["account"]["average_spend"] == pytest.approx(8.51, abs=0.01)


def test_an_invented_suggestion_is_refused_before_it_is_stored():
    llm = FakeLLM([{"content": json.dumps({"summary": "x", "suggestion": "block_the_card", "confidence": "high"})}])
    with pytest.raises(LLMError, match="suggestion"):
        assistant.narrate(llm, alert_row(), earlier=[])


def test_prose_instead_of_json_is_an_error_not_a_crash():
    with pytest.raises(LLMError, match="not JSON"):
        assistant.parse_narrative("Sure! Here is the analysis you asked for.")


def test_without_a_key_the_model_is_off_and_says_so():
    llm = LLM("https://api.deepseek.com", "deepseek-chat", None)
    assert not llm.enabled
    with pytest.raises(LLMError, match="no LLM_API_KEY"):
        llm.chat([{"role": "user", "content": "hello"}])


# --- the agent -----------------------------------------------------------------------------------


class Box(assistant.Toolbox):
    """The real dispatch, with no database behind it."""

    def __init__(self) -> None:
        super().__init__(store=None, lake="", model_info=lambda: {"live_version": "3"})

    def tool_get_alert(self, alert_id) -> dict:
        return {"alert_id": int(alert_id), "amount": 153.14}


def test_the_agent_calls_a_tool_then_answers_from_its_result():
    llm = FakeLLM([tool_call("get_alert", {"alert_id": 8}), {"content": "Alert #8 is MYR 153.14."}])

    answer = assistant.run_agent(llm, Box(), "system", [{"role": "user", "content": "What is alert 8?"}])

    assert answer["answer"] == "Alert #8 is MYR 153.14."
    assert answer["tools"] == [{"tool": "get_alert", "arguments": {"alert_id": 8}}]
    second = llm.requests[1]
    # The turn that asked for the tool goes back before its result, or the provider refuses.
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"]
    assert second[-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": json.dumps({"alert_id": 8, "amount": 153.14}),
    }


def test_an_unknown_tool_or_bad_arguments_go_back_to_the_model_as_errors():
    llm = FakeLLM(
        [
            tool_call("delete_alert", {"alert_id": 8}),
            tool_call("get_alert", {"id": 8}, call_id="c2"),
            {"content": "I can only read alerts."},
        ]
    )

    answer = assistant.run_agent(llm, Box(), "system", [{"role": "user", "content": "Delete alert 8"}])

    assert answer["answer"] == "I can only read alerts."
    assert "no tool called delete_alert" in llm.requests[1][-1]["content"]
    assert "wrong arguments" in llm.requests[2][-1]["content"]


def test_no_tool_can_write():
    """The model reads and advises. A tool name that starts with a write verb is a design change."""
    names = [tool["function"]["name"] for tool in assistant.TOOLS]
    assert not [
        name for name in names if name.split("_")[0] in {"set", "update", "delete", "decide", "write", "promote"}
    ]
    assert all(hasattr(assistant.Toolbox, f"tool_{name}") for name in names)


def test_a_repeated_tool_call_is_not_run_twice():
    """Seen live: one case note asked for the same account history 21 times."""
    calls = []

    class Counting(Box):
        def tool_get_alert(self, alert_id) -> dict:
            calls.append(alert_id)
            return super().tool_get_alert(alert_id)

    llm = FakeLLM(
        [
            tool_call("get_alert", {"alert_id": 8}),
            tool_call("get_alert", {"alert_id": 8}, call_id="c2"),
            {"content": "Done."},
        ]
    )

    answer = assistant.run_agent(llm, Counting(), "system", [{"role": "user", "content": "x"}])

    assert calls == [8]
    assert len(answer["tools"]) == 1
    assert "already have this result" in llm.requests[2][-1]["content"]


def test_an_empty_reply_is_retried_once_then_an_error():
    llm = FakeLLM([{"content": None}, {"content": "Second try."}])
    assert assistant.run_agent(llm, Box(), "system", [{"role": "user", "content": "x"}])["answer"] == "Second try."

    llm = FakeLLM([{"content": None}, {"content": "  "}])
    with pytest.raises(LLMError, match="nothing, twice"):
        assistant.run_agent(llm, Box(), "system", [{"role": "user", "content": "x"}])


def test_text_written_beside_a_tool_call_is_not_kept():
    """Written before any tool ran, it is a guess. Live, it invented a model version."""
    guess = {**tool_call("get_alert", {"alert_id": 8}), "content": "The model is v3.2 with 89% agreement."}
    llm = FakeLLM([guess, {"content": "Alert #8 is MYR 153.14."}])

    assistant.run_agent(llm, Box(), "system", [{"role": "user", "content": "x"}])

    assert "v3.2" not in json.dumps(llm.requests[1])


def test_the_last_round_must_answer_without_tools():
    llm = FakeLLM(
        [tool_call("get_alert", {"alert_id": n}, call_id=f"c{n}") for n in range(assistant.MAX_TOOL_ROUNDS - 1)]
        + [{"content": "Answer from what I have."}]
    )

    answer = assistant.run_agent(llm, Box(), "system", [{"role": "user", "content": "x"}])

    assert answer["answer"] == "Answer from what I have."
    assert llm.options[-1]["tool_choice"] == "none"
    assert all(options["tool_choice"] is None for options in llm.options[:-1])


def test_an_agent_that_never_stops_calling_tools_is_stopped():
    llm = FakeLLM([tool_call("get_alert", {"alert_id": 8}, call_id=f"c{n}") for n in range(assistant.MAX_TOOL_ROUNDS)])
    with pytest.raises(LLMError, match="no answer after"):
        assistant.run_agent(llm, Box(), "system", [{"role": "user", "content": "loop"}])


# --- the store: advice beside the decision, never in it ------------------------------------------


def narrative(suggestion: str) -> dict:
    return {"summary": "s", "suggestion": suggestion, "confidence": "high", "check": None}


def test_a_suggestion_is_stored_with_its_prompt_version(store):  # noqa: F811
    alert_id = store.raise_alert({**result("t-1"), "policy_rules": ["FP-2"], "features": SPIKE_ABROAD}, "r", {})
    saved = store.save_narrative(alert_id, narrative(LIKELY_FRAUD), "fake-model", "abcd1234")

    assert saved["ai_suggestion"] == LIKELY_FRAUD
    assert saved["ai_prompt_version"] == "abcd1234"
    assert saved["policy_rules"] == ["FP-2"]
    assert saved["features"]["amount_vs_account"] == 18.0


def test_the_ai_suggestion_never_becomes_a_label(store, tmp_path):  # noqa: F811
    """The model said fraud, the analyst said false positive. The label is the analyst's."""
    alert_id = store.raise_alert(result("t-1"), "r", {})
    store.save_narrative(alert_id, narrative(LIKELY_FRAUD), "fake-model", "v")
    store.decide(alert_id, FALSE_POSITIVE)

    export_decisions(store, str(tmp_path / "lake"))

    labels = DeltaTable(f"{tmp_path / 'lake'}/{LABELS}").to_pandas()
    assert bool(labels.iloc[0]["is_fraud"]) is False


def test_agreement_counts_firm_suggestions_and_keeps_unsure_apart(store):  # noqa: F811
    cases = [
        (LIKELY_FRAUD, CONFIRMED),
        (LIKELY_FRAUD, FALSE_POSITIVE),
        (LIKELY_FALSE_POSITIVE, FALSE_POSITIVE),
        (UNSURE, CONFIRMED),
        (LIKELY_FRAUD, None),
    ]
    for index, (suggestion, decision) in enumerate(cases):
        alert_id = store.raise_alert(result(f"t-{index}"), "r", {})
        store.save_narrative(alert_id, narrative(suggestion), "fake-model", "v")
        if decision:
            store.decide(alert_id, decision)

    agreement = store.agreement()

    # The open alert is not decided yet, so it is in neither count.
    assert agreement["decided"] == 3
    assert agreement["agreed"] == 2
    assert agreement["rate"] == pytest.approx(2 / 3)
    assert agreement["unsure"] == 1


def test_search_can_ask_for_alerts_outside_malaysia_and_always_names_a_rule(store):  # noqa: F811
    """Two answers seen live: Malaysian alerts listed as "outside Malaysia", and "no rule specified"."""
    store.raise_alert({**result("home"), "country": "MY"}, "r", {})
    store.raise_alert({**result("away"), "country": "SG"}, "r", {})
    tools = assistant.Toolbox(store, "", dict)

    abroad = tools.run("search_alerts", {"abroad": True})
    at_home = tools.run("search_alerts", {"abroad": False})

    assert [row["country"] for row in abroad["alerts"]] == ["SG"]
    assert [row["country"] for row in at_home["alerts"]] == ["MY"]
    # Raised with no stored rule, like every alert from before stage G. Search still cites one.
    assert all(row["policy_rules"] for row in abroad["alerts"] + at_home["alerts"])
    # With its title, so the model never has to guess what the id means.
    assert abroad["alerts"][0]["policy_rules"][0].startswith("FP-")
    assert " " in abroad["alerts"][0]["policy_rules"][0]


# --- the daily budget ----------------------------------------------------------------------------


def test_the_budget_stops_calls_before_any_request_is_sent():
    """Every visitor can press Ask, and each press spends the owner's credit."""
    budget = Budget(per_day=2, today=lambda: date(2026, 10, 3))
    # The base URL answers nothing. A call that got past the budget would fail with a network error.
    llm = LLM("http://127.0.0.1:9", "m", "key", budget)
    budget.take()
    budget.take()

    with pytest.raises(BudgetExceeded, match="limit of 2 AI calls"):
        llm.chat([{"role": "user", "content": "x"}])
    assert budget.snapshot() == {"used": 2, "per_day": 2}


def test_the_budget_starts_again_each_utc_day():
    clock = {"day": date(2026, 10, 3)}
    budget = Budget(per_day=1, today=lambda: clock["day"])
    budget.take()
    with pytest.raises(BudgetExceeded):
        budget.take()

    clock["day"] = date(2026, 10, 4)
    assert budget.snapshot() == {"used": 0, "per_day": 1}
    budget.take()


# --- streaming -----------------------------------------------------------------------------------


def stream(llm: FakeLLM) -> tuple[dict, list[dict]]:
    events: list[dict] = []
    answer = assistant.run_agent(llm, Box(), "system", [{"role": "user", "content": "x"}], events.append)
    return answer, events


def test_a_streamed_answer_shows_each_tool_then_the_text():
    llm = FakeLLM([tool_call("get_alert", {"alert_id": 8}), {"content": "Alert #8 is MYR 153.14."}])

    answer, events = stream(llm)

    assert events[0] == {"type": "tool", "tool": "get_alert"}
    assert "".join(event["text"] for event in events if event["type"] == "delta").strip() == answer["answer"]
    assert {"type": "discard"} not in events


def test_text_streamed_before_a_tool_call_is_taken_back():
    """The guess written beside a tool call was seen inventing numbers. A reader who saw it must see it go."""
    guess = {**tool_call("get_alert", {"alert_id": 8}), "content": "The model is v3.2."}
    llm = FakeLLM([guess, {"content": "Alert #8 is MYR 153.14."}])

    answer, events = stream(llm)

    kinds = [event["type"] for event in events]
    assert kinds.index("discard") < kinds.index("tool")
    after = events[kinds.index("discard") + 1 :]
    assert "v3.2" not in "".join(event.get("text", "") for event in after)
    assert answer["answer"] == "Alert #8 is MYR 153.14."


def test_a_case_note_gets_its_evidence_from_code_not_from_the_model():
    """Seen live: told to read the account and the similar alerts, the model read neither."""

    class Store:
        @staticmethod
        def get(alert_id) -> dict:
            return {"account_id": "ACC39002"}

    class Evidence(Box):
        def __init__(self) -> None:
            super().__init__()
            # The alert store, which the case note asks only for the account id.
            self.store = Store()

        def tool_account_history(self, account_id, limit=15) -> dict:
            return {"account_id": account_id, "average_spend": 8.51}

        def tool_similar_alerts(self, alert_id, limit=5) -> dict:
            return {"decided": {"confirmed_fraud": 3}}

    llm = FakeLLM([{"content": "## Summary\nFraud."}])

    note = assistant.case_note(llm, Evidence(), 8)

    request = llm.requests[0][-1]["content"]
    assert '"average_spend": 8.51' in request
    assert '"confirmed_fraud": 3' in request
    assert [call["tool"] for call in note["tools"]] == ["get_alert", "account_history", "similar_alerts"]
