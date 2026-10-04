"""The case note as a workflow that waits for the analyst.

    gather_evidence -> retrieve_cases -> draft_note -> analyst_review
                                            ^               |
                                            +--- return ----+--- approve --> save_note

`analyst_review` stops the run until the analyst answers. The state of every run is saved in
Postgres by the LangGraph checkpointer, so a draft waits through an API restart, and the next
request picks it up by its thread id. A draft is not a case note: `alerts.case_note` is written
only by `save_note`, after an approval.

The rules of the AI layer hold here too. Code reads the evidence, not the model. The model only
writes. Code picks the policy rule. Nothing in this graph changes `alerts.status`, so no note
becomes a training label.
"""

import json
import uuid
from contextlib import contextmanager
from typing import TypedDict

from finplat import assistant
from finplat.alerts import Store
from finplat.llm import LLM

APPROVE = "approve"
RETURN = "return"
ACTIONS = (APPROVE, RETURN)

DRAFT = "draft"
APPROVED = "approved"
# Returned more than MAX_RETURNS times. The analyst writes the note by hand.
MANUAL = "manual"
MAX_RETURNS = 3


class CaseState(TypedDict, total=False):
    alert_id: int
    evidence: dict
    draft: str
    cases: list[int]
    tools: list[dict]
    model: str
    prompt_version: str
    comment: str | None
    returns: int
    status: str


def _json_safe(value: dict) -> dict:
    """Tool results hold times and numerics. The checkpoint stores plain JSON, and nothing else."""
    return json.loads(json.dumps(value, default=str))


def build(llm: LLM, toolbox: assistant.Toolbox, store: Store, checkpointer):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    def gather_evidence(state: CaseState) -> dict:
        return {
            "evidence": _json_safe(assistant.gather_evidence(toolbox, state["alert_id"])),
            "returns": 0,
            "status": DRAFT,
        }

    def retrieve_cases(state: CaseState) -> dict:
        past = _json_safe(assistant.retrieve_cases(toolbox, state["alert_id"]))
        return {"evidence": {**state["evidence"], "past_cases": past}}

    def draft_note(state: CaseState) -> dict:
        note = assistant.draft_case_note(
            llm, toolbox, state["alert_id"], state["evidence"], state.get("comment"), state.get("draft")
        )
        return {
            "draft": note["answer"],
            "cases": note["cases"],
            "tools": note["tools"],
            "model": note["model"],
            "prompt_version": note["prompt_version"],
            "status": DRAFT,
        }

    def analyst_review(state: CaseState) -> dict:
        # A resumed run starts this node again from the top, so nothing may happen before interrupt().
        decision = interrupt({"draft": state["draft"], "cases": state["cases"], "returns": state["returns"]})
        if decision["action"] == APPROVE:
            return {"status": APPROVED, "comment": None}
        returns = state["returns"] + 1
        return {
            "status": MANUAL if returns > MAX_RETURNS else DRAFT,
            "returns": returns,
            "comment": decision["comment"],
        }

    def after_review(state: CaseState) -> str:
        return {APPROVED: "save_note", MANUAL: END}.get(state["status"], "draft_note")

    def save_note(state: CaseState) -> dict:
        store.save_case_note(state["alert_id"], state["draft"], state["model"], state["prompt_version"], state["cases"])
        return {}

    graph = StateGraph(CaseState)
    for node in (gather_evidence, retrieve_cases, draft_note, analyst_review, save_note):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "gather_evidence")
    graph.add_edge("gather_evidence", "retrieve_cases")
    graph.add_edge("retrieve_cases", "draft_note")
    graph.add_edge("draft_note", "analyst_review")
    graph.add_conditional_edges("analyst_review", after_review, ["save_note", "draft_note", END])
    graph.add_edge("save_note", END)
    return graph.compile(checkpointer=checkpointer)


@contextmanager
def _graph(dsn: str, llm: LLM, toolbox: assistant.Toolbox, store: Store):
    """One connection for each request. The run's state lives in Postgres, not in this process."""
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(dsn) as saver:
        yield build(llm, toolbox, store, saver)


def setup(dsn: str) -> None:
    """Create the checkpoint tables. Safe to run at every start."""
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.setup()


def _config(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


def _view(graph, thread: str) -> dict:
    snapshot = graph.get_state(_config(thread))
    values = snapshot.values
    return {
        "thread": thread,
        "status": values.get("status", DRAFT),
        "waiting": "analyst_review" in snapshot.next,
        "draft": values.get("draft"),
        "cases": values.get("cases", []),
        "tools": values.get("tools", []),
        "returns": values.get("returns", 0),
        "returns_left": max(0, MAX_RETURNS - values.get("returns", 0)),
    }


def start(dsn: str, llm: LLM, toolbox: assistant.Toolbox, store: Store, alert_id: int) -> dict:
    """A new run for the alert, up to the first draft. The alert keeps the thread id."""
    thread = f"case-note-{alert_id}-{uuid.uuid4().hex[:12]}"
    store.set_case_note_thread(alert_id, thread)
    with _graph(dsn, llm, toolbox, store) as graph:
        graph.invoke({"alert_id": alert_id}, _config(thread))
        return _view(graph, thread)


def view(dsn: str, llm: LLM, toolbox: assistant.Toolbox, store: Store, thread: str) -> dict:
    with _graph(dsn, llm, toolbox, store) as graph:
        return _view(graph, thread)


def review(
    dsn: str, llm: LLM, toolbox: assistant.Toolbox, store: Store, thread: str, action: str, comment: str | None
) -> dict:
    """The analyst's answer to the waiting draft. Checked before the run resumes, so a bad answer
    leaves the draft waiting as it was."""
    from langgraph.types import Command

    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    if action == RETURN and not (comment or "").strip():
        raise ValueError("a returned draft needs a comment that says what to change")
    with _graph(dsn, llm, toolbox, store) as graph:
        if not _view(graph, thread)["waiting"]:
            raise ValueError("no draft is waiting for review in this run")
        graph.invoke(Command(resume={"action": action, "comment": (comment or "").strip() or None}), _config(thread))
        return _view(graph, thread)
