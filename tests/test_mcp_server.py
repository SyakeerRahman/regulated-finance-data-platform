"""The MCP server, driven by a real MCP client in process. No subprocess and no model."""

import json

import anyio
from mcp import Client

from finplat import assistant, mcp_server
from finplat.catalog import CATALOG
from tests.test_ai import SPIKE_ABROAD, checked_lake  # noqa: F401 - pytest finds fixtures by name
from tests.test_alerts import result, store, test_dsn  # noqa: F401


def toolbox(alerts, lake: str) -> assistant.Toolbox:
    return assistant.Toolbox(alerts, lake, model_info=lambda: {"live_version": "3"})


def session(box: assistant.Toolbox, work):
    """Connect a client to the server over the toolbox, and run `work(client)`."""

    async def main():
        async with Client(mcp_server.build(box)) as client:
            return await work(client)

    return anyio.run(main)


def payload(answer) -> dict:
    return json.loads(answer.content[0].text)


def test_the_server_lists_every_toolbox_tool_as_read_only():
    listed = session(toolbox(None, ""), lambda client: client.list_tools()).tools

    assert [tool.name for tool in listed] == [tool["function"]["name"] for tool in assistant.TOOLS]
    assert all(tool.annotations.read_only_hint for tool in listed)


def test_a_client_reads_an_alert_and_the_quality_results(store, checked_lake):  # noqa: F811
    alert_id = store.raise_alert({**result("t-1"), "policy_rules": ["FP-2"], "features": SPIKE_ABROAD}, "r", {})

    async def work(client):
        alert = await client.call_tool("get_alert", {"alert_id": alert_id})
        quality = await client.call_tool("quality_history", {"days": 7})
        return alert, quality

    alert, quality = session(toolbox(store, checked_lake), work)

    assert not alert.is_error and payload(alert)["alert_id"] == alert_id
    newest = payload(quality)["batches"][0]
    assert newest["batch_id"] == "2026-09-02" and newest["stopped_the_run"]


def test_a_tool_error_is_marked_as_an_error():
    answer = session(toolbox(None, ""), lambda client: client.call_tool("describe_table", {"name": "gold"}))
    assert answer.is_error
    assert "there is no table gold" in payload(answer)["error"]


def test_no_write_tool_reaches_the_client():
    """Ask AI and the MCP client share one list, and nothing in it can decide an alert."""
    answer = session(toolbox(None, ""), lambda client: client.call_tool("update_alert", {"alert_id": 1}))
    assert answer.is_error and "no tool called update_alert" in payload(answer)["error"]


def test_the_catalog_and_the_policy_are_resources():
    async def work(client):
        listed = await client.list_resources()
        gold = await client.read_resource(f"{mcp_server.CATALOG_PREFIX}gold/transaction_features")
        rules = await client.read_resource(mcp_server.POLICY_URI)
        return listed, gold, rules

    listed, gold, rules = session(toolbox(None, ""), work)

    uris = {str(resource.uri) for resource in listed.resources}
    assert mcp_server.POLICY_URI in uris
    assert {f"{mcp_server.CATALOG_PREFIX}{table}" for table in CATALOG} <= uris
    assert "amount_vs_account" in json.loads(gold.contents[0].text)["columns"]
    assert json.loads(rules.contents[0].text)["rules"]


def test_the_investigation_prompt_names_the_alert_and_leaves_the_decision_to_a_person():
    async def work(client):
        prompts = await client.list_prompts()
        prompt = await client.get_prompt(mcp_server.INVESTIGATE, {"alert_id": "412"})
        return prompts, prompt

    prompts, prompt = session(toolbox(None, ""), work)

    assert [p.name for p in prompts.prompts] == [mcp_server.INVESTIGATE]
    text = prompt.messages[0].content.text
    assert "alert #412" in text and "Do not decide the alert" in text
