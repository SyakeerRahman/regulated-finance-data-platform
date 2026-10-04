# Backlog: grounded case notes with analyst approval

Status: **approved on 2026-10-03, in Jira as Epic SCRUM-30 with SCRUM-31 to SCRUM-37.** See the decisions at the end.
Written 2026-10-03. Component in Jira: `regulated-finance-data-platform`.

## Epic

**An analyst gets a case note that cites similar past cases, and approves the note before it is saved.**

Today the case note agent reads the alert, the account and the similar alerts, then writes the
note and saves it at once. Two things are missing:

1. It cannot find past cases by meaning. `Store.similar` compares numbers and flags only.
2. No person checks the note before it is stored.

This epic adds retrieval over past cases (RAG, with pgvector) and a review step that stops the
workflow until the analyst approves (LangGraph). The rules from
`brain/decisions/2026-10-03-the-llm-narrates-and-code-cites.md` stay: code picks the policy rule,
the model only advises, and an AI suggestion never becomes a training label.

Estimate: about 23 hours, or 3 weekends.

## Stories

| # | Jira | Type | Title | Hours | Depends on |
|---|---|---|---|---|---|
| 1 | SCRUM-31 | Task | Postgres runs pgvector in every environment | 3 | - |
| 2 | SCRUM-32 | Story | Embeddings are a setting, like the chat model | 2 | - |
| 3 | SCRUM-33 | Story | Every closed case is indexed as a vector | 3 | 1, 2 |
| 4 | SCRUM-34 | Story | The case note cites similar past cases (RAG) | 4 | 3 |
| 5 | SCRUM-35 | Story | The case note is a LangGraph workflow that waits for the analyst | 6 | 4 |
| 6 | SCRUM-36 | Story | The analyst approves or returns the note on the dashboard | 4 | 5 |
| 7 | SCRUM-37 | Task | Record the decision and update the docs | 1 | 1 to 6 |

### 1. Postgres runs pgvector in every environment

The local stack, CI and `deploy/compose.yml` use `postgres:18-alpine`. pgvector needs another image.

- Change the Postgres image in `docker-compose.yml`, `deploy/compose.yml` and `.github/workflows/ci.yml`.
- Move the data with `pg_dump` and a restore into a new volume. Do not mount the old volume into
  the new image. Alpine and Debian sort text differently, and a restore rebuilds every index.
- Run `create extension if not exists vector` in the schema setup in `finplat/alerts.py`.

Acceptance:
- `create extension vector` succeeds in all 3 environments.
- The alert count and the decision count are the same before and after the move.
- `deploy/backup.sh` and `deploy/restore.sh` pass the rehearsal against MinIO again.
- All tests pass in CI.

### 2. Embeddings are a setting, like the chat model

- Add `finplat/embed.py`: one POST to `{EMBED_BASE_URL}/embeddings` in the OpenAI format. No SDK.
- Add `EMBED_BASE_URL`, `EMBED_MODEL` and `EMBED_DIM` to `Settings`. Default: OpenRouter,
  `openai/text-embedding-3-small`, 1536. Use the same key as `LLM_API_KEY`.
- Keep `EMBED_DIM` at 2000 or less. A pgvector HNSW index on `vector` takes at most 2000
  dimensions. `qwen/qwen3-embedding-4b` (2560) and `-8b` (4096) would need `halfvec`.
- Count each embedding call in the daily budget, or in its own budget.
- Startup stops when the length of a returned vector is not `EMBED_DIM`.
- Tests use a fake embedder. The suite never calls a real one.

Acceptance:
- With no key, the feature reports itself off and every other part works.
- A test proves the vector length check.

### 3. Every closed case is indexed as a vector

- New table `case_vectors`: `alert_id`, `text`, `outcome`, `outcome_source`, `known_at`,
  `embed_model`, `embedding vector(EMBED_DIM)`.
- The indexed text is the alert summary, the policy rule, the top SHAP reasons and the case note
  when there is one.
- The outcome is the analyst decision. With no decision, the outcome is the label from
  `silver/labels`, but only after its `labelled_at`.
- Index on decision, and with a backfill command for existing alerts.
- Use an exact search first. Add an HNSW index only when a measurement shows it is needed.

Acceptance:
- **A retrieved case is never one whose outcome was unknown at the time of the alert.** A test
  builds a case with a later `known_at` and asserts that it is not returned. This is the same
  leak rule as `amount_vs_account`.
- The backfill can run twice with the same result.

### 4. The case note cites similar past cases (RAG)

- Retrieve the 5 nearest closed cases for the alert, with their outcomes.
- Add them to the evidence that `assistant.case_note` collects in code.
- Add a read-only tool `similar_cases` for the Ask AI tab.
- The note names the alert ids it used. The dashboard links them.
- Grade it: extend `finplat/ai_eval.py` to compare the suggestion with and without retrieval, on a
  seed that was not used for tuning.

Acceptance:
- Each note stores the ids of the cases it cited.
- The `ai_eval` result for both modes is in the commit message.

### 5. The case note is a LangGraph workflow that waits for the analyst

Graph: `gather_evidence` -> `retrieve_cases` -> `draft_note` -> `analyst_review` (interrupt).
From `analyst_review`: approve goes to `save_note`. Return with a comment goes back to `draft_note`.

- The graph state is saved by the Postgres checkpointer, so a review survives an API restart.
- `gather_evidence` stays code. Do not let the model choose what evidence to read.
- API: `POST /api/alerts/{id}/case-note` starts a run and returns the draft.
  `POST /api/alerts/{id}/case-note/review` resumes it with `approve` or `return` and a comment.
- A draft is not a case note until it is approved.
- At most 3 returns. After that, the analyst writes the note by hand.

Acceptance:
- A test runs the graph with the fake model: draft, return, redraft, approve, saved.
- A test restarts between the draft and the approval, and the run resumes.
- The existing test that a suggestion never becomes a label still passes.

### 6. The analyst approves or returns the note on the dashboard

- The case note panel shows `Draft` until approval.
- Buttons: **Approve**, and **Return with comment**, which needs a comment.
- Show the cited past cases with their outcomes. Each one opens that alert.
- The approved note shows who approved it and when.

Acceptance:
- A screenshot of the whole flow on the live stack.
- Ask AI and the alert card still work when the AI is off.

### 7. Record the decision and update the docs

- An addendum to `2026-10-03-the-llm-narrates-and-code-cites.md`: retrieval is now worth adding,
  because the corpus is free text and OpenRouter serves embeddings with the key already in use.
- Why pgvector and not Qdrant or Pinecone: it is inside the Postgres that already runs, no new
  container, and no card data leaves the server for a vector vendor.
- The answers to the 3 dependency questions in `AGENTS.md`, for `langgraph` and
  `langgraph-checkpoint-postgres`.
- `README.md`: the case data that goes to OpenRouter. The data is synthetic. A real bank could not
  send it.

## Open questions, ranked

1. **Which Postgres image?** `pgvector/pgvector:pg18` is Debian and about 140 MB larger. The
   alternative is our own Dockerfile that builds pgvector on `postgres:18-alpine`, about 20
   lines. Recommendation: the official image. A dump and restore removes the sort order risk.
2. **What fills the corpus?** Only a few alerts have a case note or a decision today.
   Recommendation: index every alert with an AI summary, and use the delayed label as the outcome
   when no analyst decided. Without this, retrieval returns almost nothing.
3. **Is LangChain needed?** `langgraph` brings `langchain-core`. The plan does not use any other
   LangChain package, and the model call stays in `finplat/llm.py`. Recommendation: no
   `langchain` package. If the CV must name it, the honest line is "LangGraph, built on LangChain core".
4. **Separate budget for embeddings?** One case note uses 1 embedding call and up to 6 chat
   calls. Recommendation: count both in `LLM_DAILY_CALLS` for now.

## Assumptions I made

- Checked on 2026-10-03 with one request each: the OpenRouter key in `.env` calls `/embeddings`.
  `openai/text-embedding-3-small` returns 1536 numbers, `qwen/qwen3-embedding-4b` 2560 and
  `qwen/qwen3-embedding-8b` 4096. `qwen/qwen3-embedding-0.6b` is no longer listed (HTTP 404).
  OpenRouter lists 33 embedding models, 3 of them free.
- The VPS disk budget can take the larger Postgres image: about 5.6 GB of 8 GB is used now.
- The dependency policy allows `langgraph`, because the Postgres checkpointer gives durable
  pause and resume. Writing that by hand is more than 30 lines and easy to get wrong.

## Tickets gate decisions

Approved by the owner on 2026-10-03. All 4 recommendations above are accepted:

1. Postgres image: `pgvector/pgvector:pg18`. Move the data with `pg_dump` and a restore.
2. Corpus: every alert with an AI summary. The outcome is the analyst decision, or the label
   from `silver/labels` once its `labelled_at` has passed.
3. No `langchain` package. Only `langgraph`, `langgraph-checkpoint-postgres` and what they bring.
4. Embedding calls count in `LLM_DAILY_CALLS`. No separate budget.

## Change to decision 2, during SCRUM-33

Decided by the owner on 2026-10-04. Labels arrive 30 to 90 days after the payment, and the lake
starts on 2026-09-24, so no label is known yet. With only known outcomes, the corpus held 1 case.

The corpus is now every alert with an AI summary. Each case has an outcome and its source:

- `analyst`: the analyst decision, known at `decided_at`.
- `label`: the label from `silver/labels`, known at `labelled_at`.
- `simulated`: the true answer, written by a demo command. It lives only in `case_vectors`, never
  in `alerts.status`, so it can never become a training label. Known 1 hour after its alert, as if
  a team decided each case within the hour. Changed by the owner on 2026-10-04 during SCRUM-34:
  known "when the command ran" hid every simulated outcome from every older alert, so the grading
  of retrieval on older alerts measured nothing.
- No outcome yet: the case is returned as pending.

The leak rule now applies to the outcome: a search for an alert shows an outcome only when it
was known before that alert was raised. A case raised after that alert is not returned.
