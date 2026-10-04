# Backlog: a data catalog, and the platform tools over MCP

Status: **approved on 2026-10-04, in Jira as Epic SCRUM-38 with SCRUM-39 to SCRUM-42.** See the decisions at the end.
Written 2026-10-04. Component in Jira: `regulated-finance-data-platform`.

## Epic

**Any MCP client, for example Claude Desktop, can ask the platform about its data and get answers from the real tables.**

Today the 10 read-only tools in `finplat/assistant.py` work only in the Ask AI tab. Two things are missing:

1. No file says what each table and column means. The Data tab shows rows, but not their meaning.
2. No tool reads the quality results. The checks run each day and are stored, but the AI cannot see them.

This epic adds a data catalog in code, two new read-only tools, and an MCP server that serves all
the tools over stdio. The rules from `brain/decisions/2026-10-03-the-llm-narrates-and-code-cites.md`
stay: code picks the policy rule, and no tool writes.

Estimate: about 9 hours, or 1 weekend.

## Stories

| # | Jira | Type | Title | Hours | Depends on |
|---|---|---|---|---|---|
| 1 | SCRUM-39 | Story | Every lake table and column has a description in the catalog | 3 | - |
| 2 | SCRUM-40 | Story | The AI can read the quality results and the catalog | 1.5 | 1 |
| 3 | SCRUM-41 | Story | An MCP client can use the platform tools | 3.5 | 2 |
| 4 | SCRUM-42 | Task | Record the decision and update the docs | 1 | 1 to 3 |

### 1. Every lake table and column has a description in the catalog

- Add `finplat/catalog.py`. It holds one entry for each table in `LAKE_TABLES`: bronze,
  quarantine, silver, labels, gold and quality.
- Each table entry has a purpose, the grain (what one row is), the function that writes it, how
  the write stays idempotent, and the retention in days.
- Each column entry has one sentence that tells what the value means, its unit, and its rules.
  Example: `amount_vs_account` is the amount divided by the mean of the account's earlier
  transactions only. The first transaction of an account is `1.0`.
- The retention days come from `RETENTION_DAYS` in `finplat/retention.py`. Do not write the numbers twice.
- The Data tab shows the column description as a tooltip on each column heading.

Acceptance:
- A test reads the schema of each Delta table and fails when a column has no catalog entry.
- A test fails when the catalog names a column that the table does not have.
- Both tests run in CI.

### 2. The AI can read the quality results and the catalog

- Add `tool_quality_history(days=7)` to `Toolbox`. It calls `quality.history`. It reads the
  stored results and does not run the checks again, so the answer is what the gate decided that day.
- Add `tool_describe_table(name)` to `Toolbox`. It returns the catalog entry. With no name, it
  returns the list of tables and their purpose.
- Add both tools to the tool list, so the Ask AI tab can use them.

Acceptance:
- A test with the fake model asks "why did the batch fail" and the agent calls `quality_history`.
- A test calls `describe_table` with an unknown name and gets an error the model can read, not a crash.

### 3. An MCP client can use the platform tools

- Add `finplat/mcp_server.py`. It serves the 12 `Toolbox` tools over stdio. Each MCP tool calls
  `Toolbox.run`. Do not write the tool logic a second time.
- The tool names, descriptions and argument schemas come from the existing tool list in
  `assistant.py`. One list feeds the Ask AI tab and the MCP server.
- Resources: `finplat://catalog/{table}` and `finplat://policy/rules`.
- Prompt: `investigate_alert(alert_id)`, a fixed investigation recipe.
- No tool writes. There is no approve tool. The analyst approves on the dashboard.
- Run it with `docker exec -i finplat-api python -m finplat.mcp_server`, so it uses the same
  settings and network as the API.

Acceptance:
- A test starts the server in process, lists the tools, and calls `get_alert` and
  `quality_history`.
- A screenshot of Claude Desktop that answers "why did yesterday's batch fail, and which alerts
  came from it?" on the local stack.

### 4. Record the decision and update the docs

- An addendum to `2026-10-03-the-llm-narrates-and-code-cites.md`: the tools now also go over MCP,
  and the rule "no tool writes" applies to every client, not only the Ask AI tab.
- The answers to the 3 dependency questions in `AGENTS.md`, for the `mcp` package.
- `README.md` and `CLAUDE.md`: how to connect Claude Desktop, with the config block.

## Open questions, ranked

1. **The `mcp` package, or our own stdio server?** MCP is a published standard: JSON-RPC over
   stdio, with a handshake and capability negotiation. The dependency policy allows a library for
   "a rewrite of a standard". Recommendation: the official `mcp` package.
2. **Does the catalog also cover the Postgres tables** (`alerts`, `case_vectors`)?
   Recommendation: no, lake tables only for now. Add Postgres in a later story if needed.
3. **Run the server on the VPS too?** Stdio needs no open port. Over SSH, a client can run it
   with `ssh vps docker compose exec -T api ...`. Recommendation: local only for this epic.

## Assumptions I made

- `quality.history` returns the stored check results, the same data the dashboard shows
  (`api/main.py` imports it).
- `Toolbox` needs only the `Store`, the lake path and `model_info`, so the MCP server can build
  one the same way the API does.
- The `mcp` package installs in the API image without a conflict. The build runs `pip check`.

## Tickets gate decisions

Approved by the owner on 2026-10-04. All 3 recommendations above are accepted:

1. Library: the official `mcp` package.
2. Catalog scope: the lake tables only. The Postgres tables wait for a later story.
3. Where it runs: local only in this epic.

## Change to story 3, during SCRUM-41

Found on 2026-10-04. An MCP client starts the server with only a few environment variables, and
without the rest the Docker CLI cannot find its compose plugin: `docker compose exec` failed with
"unknown shorthand flag: 'T'". The command is now `docker exec -i finplat-api ...`, and the API
container has the fixed name `finplat-api`, as Postgres has `finplat-postgres`.
