"""The case note workflow: draft, return, redraft, approve, and a restart in the middle.

The checkpoint lives in the Postgres test database. Every call opens its own connection and builds
its own graph, as each API request does, so a test that passes state between calls passes it
through Postgres alone. The model is fake.
"""

import pytest

from finplat import assistant, case_flow
from finplat.alerts import OPEN, export_decisions
from tests.test_ai import FakeLLM
from tests.test_alerts import result, store, test_dsn  # noqa: F401 - pytest finds fixtures by name


class Evidence(assistant.Toolbox):
    """Fixed evidence over a real alert store. Counts what it was asked for."""

    def __init__(self, store) -> None:  # noqa: F811
        super().__init__(store=store, lake="", model_info=dict)
        self.reads: list[str] = []

    def run(self, name: str, arguments: dict) -> object:
        self.reads.append(name)
        return {
            "get_alert": {"alert_id": arguments.get("alert_id"), "amount": 411.13},
            "account_history": {"average_spend": 8.51},
            "similar_alerts": {"decided": {"confirmed_fraud": 3}},
            "similar_cases": {"cases": [{"alert_id": 567, "outcome": "confirmed_fraud"}]},
        }[name]


@pytest.fixture
def flow(store, test_dsn):  # noqa: F811
    case_flow.setup(test_dsn)
    alert_id = store.raise_alert(result("t-1"), "reason", {})
    return test_dsn, store, alert_id


def note(text: str) -> dict:
    return {"content": text}


def test_draft_return_redraft_approve_and_only_then_saved(flow):
    dsn, store, alert_id = flow  # noqa: F811
    llm = FakeLLM([note("## Summary\nFirst, as in #567."), note("## Summary\nSecond, with the account, as in #567.")])
    toolbox = Evidence(store)

    first = case_flow.start(dsn, llm, toolbox, store, alert_id)
    assert first["waiting"] and first["status"] == case_flow.DRAFT
    assert first["draft"] == "## Summary\nFirst, as in #567."
    assert first["cases"] == [567]
    # Code read the evidence, in order, before the model wrote a word.
    assert toolbox.reads == ["get_alert", "account_history", "similar_alerts", "similar_cases"]
    # A draft is not a case note.
    assert store.get(alert_id)["case_note"] is None
    assert store.get(alert_id)["case_note_thread"] == first["thread"]

    second = case_flow.review(dsn, llm, toolbox, store, first["thread"], case_flow.RETURN, "Mention the account.")
    assert second["waiting"] and second["returns"] == 1
    assert second["draft"] == "## Summary\nSecond, with the account, as in #567."
    redraft_request = llm.requests[1][-1]["content"]
    assert "Comment: Mention the account." in redraft_request
    assert "First, as in #567." in redraft_request
    assert store.get(alert_id)["case_note"] is None

    done = case_flow.review(dsn, llm, toolbox, store, first["thread"], case_flow.APPROVE, None)
    assert done["status"] == case_flow.APPROVED and not done["waiting"]
    saved = store.get(alert_id)
    assert saved["case_note"] == "## Summary\nSecond, with the account, as in #567."
    assert saved["case_note_cases"] == [567]
    # An approved note is still not a decision, and so not a label.
    assert saved["status"] == OPEN


def test_a_draft_waits_through_a_restart(flow, tmp_path):
    dsn, store, alert_id = flow  # noqa: F811
    thread = case_flow.start(dsn, FakeLLM([note("## Summary\nDraft.")]), Evidence(store), store, alert_id)["thread"]

    # A new process: a new model client, a new toolbox, no memory. Only the thread id is known.
    after = case_flow.view(dsn, FakeLLM([]), Evidence(store), store, thread)
    assert after["waiting"] and after["draft"] == "## Summary\nDraft."

    case_flow.review(dsn, FakeLLM([]), Evidence(store), store, thread, case_flow.APPROVE, None)
    assert store.get(alert_id)["case_note"] == "## Summary\nDraft."
    assert export_decisions(store, str(tmp_path / "lake")) == 0


def test_after_three_returns_the_analyst_writes_it(flow):
    dsn, store, alert_id = flow  # noqa: F811
    llm = FakeLLM([note(f"## Summary\nDraft {number}.") for number in range(1, 5)])
    toolbox = Evidence(store)
    thread = case_flow.start(dsn, llm, toolbox, store, alert_id)["thread"]

    for number in range(1, 4):
        state = case_flow.review(dsn, llm, toolbox, store, thread, case_flow.RETURN, "Shorter.")
        assert state["waiting"] and state["returns_left"] == 3 - number

    last = case_flow.review(dsn, llm, toolbox, store, thread, case_flow.RETURN, "Still too long.")
    assert last["status"] == case_flow.MANUAL and not last["waiting"]
    # Four drafts, one for the start and one for each return the limit allowed. No fifth.
    assert len(llm.requests) == 4
    assert store.get(alert_id)["case_note"] is None


def test_a_bad_answer_leaves_the_draft_waiting(flow):
    dsn, store, alert_id = flow  # noqa: F811
    thread = case_flow.start(dsn, FakeLLM([note("## Summary\nDraft.")]), Evidence(store), store, alert_id)["thread"]

    with pytest.raises(ValueError, match="needs a comment"):
        case_flow.review(dsn, FakeLLM([]), Evidence(store), store, thread, case_flow.RETURN, "  ")
    with pytest.raises(ValueError, match="action must be one of"):
        case_flow.review(dsn, FakeLLM([]), Evidence(store), store, thread, "delete", None)
    assert case_flow.view(dsn, FakeLLM([]), Evidence(store), store, thread)["waiting"]

    case_flow.review(dsn, FakeLLM([]), Evidence(store), store, thread, case_flow.APPROVE, None)
    with pytest.raises(ValueError, match="no draft is waiting"):
        case_flow.review(dsn, FakeLLM([]), Evidence(store), store, thread, case_flow.APPROVE, None)
