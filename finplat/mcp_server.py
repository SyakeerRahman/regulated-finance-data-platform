"""The platform's read-only tools over MCP, for Claude Desktop, Claude Code or any MCP client.

    docker exec -i finplat-api python -m finplat.mcp_server

Not `docker compose exec`: an MCP client starts the server with only a few environment
variables, and without the rest the Docker CLI cannot find its compose plugin.

It serves the same `assistant.TOOLS` as the Ask AI tab, and each call goes through `Toolbox.run`.
One list and one dispatch, so the two cannot drift apart. No tool writes: a decision on an alert
is made by a person on the Alerts tab, whichever client asks.

The transport is stdio, so the server opens no port. The client starts it and owns its lifetime.
"""

import json

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from finplat import assistant, catalog, policy

POLICY_URI = "finplat://policy/rules"
CATALOG_PREFIX = "finplat://catalog/"
INVESTIGATE = "investigate_alert"

INSTRUCTIONS = (
    "finplat is a fraud detection platform for a Malaysian card issuer. All data is synthetic. "
    "Amounts are in MYR and times are UTC. The tools read data and none of them write. "
    "Use a tool for every number, alert, account or rule you mention."
)


def _text(value: object) -> str:
    return json.dumps(value, default=str)


def build(toolbox: assistant.Toolbox) -> Server:
    """The MCP server over one toolbox. Tests pass a toolbox with a test lake behind it."""

    tools = [
        types.Tool(
            name=tool["function"]["name"],
            description=tool["function"]["description"],
            input_schema=tool["function"]["parameters"],
            annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False),
        )
        for tool in assistant.TOOLS
    ]

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        # The tools read Postgres and Delta with blocking calls, so they run off the event loop.
        result = await anyio.to_thread.run_sync(toolbox.run, params.name, params.arguments or {})
        failed = isinstance(result, dict) and "error" in result
        return types.CallToolResult(content=[types.TextContent(text=_text(result))], is_error=failed)

    async def list_resources(ctx, params) -> types.ListResourcesResult:
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri=POLICY_URI,
                    name="fraud policy",
                    description=f"The rules of {policy.POLICY_NAME}. Code picks the rule an alert cites.",
                    mime_type="application/json",
                ),
                *[
                    types.Resource(
                        uri=f"{CATALOG_PREFIX}{table}",
                        name=table,
                        description=entry.purpose,
                        mime_type="application/json",
                    )
                    for table, entry in catalog.CATALOG.items()
                ],
            ]
        )

    async def read_resource(ctx, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
        uri = str(params.uri)
        if uri == POLICY_URI:
            body = toolbox.tool_policy_rules()
        elif uri.removeprefix(CATALOG_PREFIX) in catalog.CATALOG:
            body = catalog.describe(uri.removeprefix(CATALOG_PREFIX))
        else:
            raise ValueError(f"there is no resource {uri}")
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=uri, mime_type="application/json", text=_text(body))]
        )

    async def list_prompts(ctx, params) -> types.ListPromptsResult:
        return types.ListPromptsResult(
            prompts=[
                types.Prompt(
                    name=INVESTIGATE,
                    description="Investigate one alert from its evidence, and say what a person should check next.",
                    arguments=[types.PromptArgument(name="alert_id", description="For example 412", required=True)],
                )
            ]
        )

    async def get_prompt(ctx, params: types.GetPromptRequestParams) -> types.GetPromptResult:
        if params.name != INVESTIGATE:
            raise ValueError(f"there is no prompt {params.name}")
        alert_id = (params.arguments or {}).get("alert_id", "")
        steps = (
            f"Investigate alert #{alert_id}.\n\n"
            f"1. Read it with get_alert. Note the score, the threshold and the top SHAP reasons.\n"
            f"2. Read the account with account_history. Is the payment unusual for this account?\n"
            f"3. Read the policy rule the alert cites with policy_rules. Quote its title.\n"
            f"4. Read similar_cases and similar_alerts. Say what happened to the closest ones.\n"
            f"5. Answer in 5 lines or fewer: what happened, the evidence for fraud, the evidence against, "
            f"and one thing a person should check. Do not decide the alert. An analyst does that on the Alerts tab."
        )
        return types.GetPromptResult(
            description=f"Investigate alert #{alert_id}",
            messages=[types.PromptMessage(role="user", content=types.TextContent(text=steps))],
        )

    return Server(
        "finplat",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_read_resource=read_resource,
        on_list_prompts=list_prompts,
        on_get_prompt=get_prompt,
    )


def from_settings() -> assistant.Toolbox:
    """A toolbox over the services the settings name, the same ones the API reads."""
    from finplat import embed, model_report
    from finplat.alerts import Store
    from finplat.llm import Budget
    from finplat.registry import live_version, production_threshold
    from finplat.settings import get_settings

    settings = get_settings()

    def model_info() -> dict:
        uri = settings.mlflow_tracking_uri
        return {
            "live_version": live_version(uri),
            "threshold": production_threshold(uri),
            "versions": model_report.version_rows(uri),
        }

    # Its own budget: the count lives in each process, so this one does not share the API's.
    embedder = embed.from_settings(settings, Budget(settings.llm_daily_calls))
    return assistant.Toolbox(Store(settings.postgres_dsn), settings.lake_uri, model_info, embedder)


async def serve() -> None:
    server = build(from_settings())
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(serve)
