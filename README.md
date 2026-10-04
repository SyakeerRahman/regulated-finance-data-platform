# regulated-finance-data-platform

A fraud-scoring data platform for a regulated finance setting. Airflow loads transactions into
Delta Lake tables. MLflow trains and registers a fraud model. A live service scores each
transaction and shows the alerts on a dashboard. An AWS Lambda function scores one transaction
with the same model. GitHub Actions tests each push and publishes the container images. The
server stack runs on Docker Compose. The data is synthetic. The brief is in
[docs/brief.md](docs/brief.md).

## Stack

| Layer | Choice |
| ----- | ------ |
| Orchestration | Apache Airflow |
| Storage | Delta Lake (delta-rs) |
| Model registry | MLflow |
| Live service | FastAPI and React, with Postgres for the alerts |
| AI layer | Any OpenAI-format chat API, DeepSeek by default |
| Serving, one transaction | AWS Lambda |
| Infrastructure | Terraform for the Lambda, Docker Compose on one VPS |
| CI/CD | GitHub Actions, with images in GitHub Container Registry |

## Repo layout

```text
regulated-finance-data-platform/
  AGENTS.md                 # agent instructions (read first)
  README.md
  finplat/                  # the pipeline package
    settings.py             # the one settings module. Reads LAKE_URI
    generate.py             # synthetic transactions and their late labels
    pipeline.py             # bronze, silver (quarantine + merge + labels), gold
    train.py                # fit a model on the labels known by a cutoff date
    registry.py             # list the model versions, promote one to production
    features.py             # the one definition of a feature, batch and live
    domain.py               # the vocabulary. Imports nothing, so the Lambda can carry it
    export.py               # the live model as plain JSON, for the Lambda
    quality.py              # the checks that stop a bad batch reaching gold
    catalog.py              # what each lake table and column means. A test keeps it true
    mcp_server.py           # the read-only AI tools, over MCP, for Claude Desktop and others
    retention.py            # delete old batches, VACUUM, delete old Airflow logs
    explain.py              # SHAP, turned into a sentence an analyst reads
    alerts.py               # alerts and analyst decisions, in Postgres
    run.py                  # one day without Airflow
  api/                      # the live path: generate, score, stream to the browser
  web/                      # the dashboard. React, Vite, Tailwind. Builds into api/static
  lambda_fn/                # AWS Lambda: score one transaction, standard library only
  terraform/                # the Lambda, its role, its log group, its URL
  scripts/                  # build the Lambda zip
  dags/                     # Airflow DAGs. Thin: each task calls finplat
  tests/                    # pytest, against a temporary lake
  docs/                     # the brief
  data/lake/                # Delta tables (gitignored)
  Dockerfile                # Airflow image, with finplat and dags copied in
  Dockerfile.api            # the live service image, with the dashboard built in
  docker-compose.yml        # the local stack: Airflow, MLflow, Postgres
  deploy/                   # the server stack, and the scripts that run on the server
    compose.yml             # Airflow, API, Postgres, MLflow, each with a memory limit
    bootstrap.sh            # fill an empty server: 3 days, one training run, promote
    backup.sh               # nightly: Postgres and MLflow, uploaded to a bucket
    restore.sh              # put one backup back
    s3.sh                   # upload and download, for any S3-compatible bucket
    .env.example            # the settings the server needs
  .github/workflows/ci.yml  # tests, Terraform checks, then the two images
```

## Prerequisites

| Tool | Version | Used for |
| ---- | ------- | -------- |
| uv | 0.12 or later | Python environment and tests |
| Docker Desktop | 29 or later | Airflow, MLflow, Postgres |
| Node.js | 24 or later | The dashboard build |

## Running locally

1. Make the settings file: `echo LAKE_URI=data/lake > .env`
2. Install the Python environment: `uv sync`
3. Start Airflow, MLflow and Postgres: `docker compose up -d --build`
4. Run the tests: `uv run pytest`
5. Run 3 days of the pipeline. The model needs 3 days of history.

   ```bash
   uv run python -m finplat.run 2026-09-24
   uv run python -m finplat.run 2026-09-25
   uv run python -m finplat.run 2026-09-26
   ```

6. Train a model: `uv run python -m finplat.train 2027-01-01`
7. List the versions: `uv run python -m finplat.registry list`
8. Promote one version: `uv run python -m finplat.registry promote <version>`
9. Build the dashboard: `cd web && npm install && npm run build`
10. Start the service: `uv run uvicorn api.main:app --port 8097`
11. Open http://localhost:8097 and press Start.

Airflow is at http://localhost:8095 and MLflow is at http://localhost:8096.

`cd web && npm run dev` serves the dashboard on port 5173 with live reload and forwards `/api`
to uvicorn. `npm run build` writes into `api/static`, which is gitignored, so one service
answers in production.

## The tables

| Table | Written by | Holds |
|---|---|---|
| `bronze/transactions` | `load_bronze` | Each batch as received, partitioned by `batch_id` |
| `silver/quarantine` | `refine_silver` | Rejected rows, with the reason |
| `silver/transactions` | `refine_silver` | Valid rows, one per `transaction_id`, upserted with MERGE |
| `silver/labels` | `load_labels` | The verdict on each transaction, and the date it was known |
| `gold/transaction_features` | `build_gold` | Model features. Account history uses earlier rows only |
| `quality/checks` | `record` | One row per check per batch, with the value and the threshold |

A rerun of a batch replaces that batch. It does not add a second copy.

`finplat/catalog.py` says what each table and each column means, and how long a table keeps a
batch. The Data tab shows a column's meaning when you hover its heading. `tests/test_catalog.py`
compares the catalog with the real Delta schemas, so a new column without a description fails CI.

## The label is not a column on the transaction

No table of transactions holds `is_fraud`. A bank does not know the answer when a payment
arrives. The answer comes 30 to 90 days later, from a chargeback, a customer call, or an analyst.

`silver/labels` holds the answer with a `labelled_at` date. `training_frame(lake, as_of)` joins
the two and drops every transaction that nobody had judged on that date. A model trained today
therefore cannot read a verdict that arrives next month.

## What this data is not

The data is synthetic. `finplat/generate.py` decides fraud first, then draws the amount, the
hour, the country, the category and the channel from different distributions. The generator does
not detect fraud. It declares fraud, and then it writes the evidence.

Three limits follow, and they apply to every result in this repository:

1. The model scores well because the pattern is clean and deliberate. Real fraud overlaps with
   real spending, and a real fraudster changes tactics as soon as a rule catches them.
   An earlier version was worse: it drew every honest hour from 6 to 23, so nothing legitimate
   happened at 3am and `is_night` was the answer rather than a signal. Honest hours now follow a
   daily curve, and night holds about 3.5% of legitimate volume and 20.9% fraud.
2. The label delay is a simplification. A real chargeback window is often 120 days, and a
   customer call can arrive within hours.
3. No public dataset of labelled card transactions exists, because card scheme rules and privacy
   law forbid it. Synthetic data is the only option, not a shortcut.

## Data quality

A pipeline rarely fails loudly. It usually succeeds and produces rubbish. So the checks that
matter are not the row rules in `refine_silver`. They are the ones that compare today against
the days before it.

| Check | Rule | Severity |
| ----- | ---- | -------- |
| schema | Every column is present with the expected type | Critical |
| freshness | The newest row landed in the last 25 hours, and is not dated ahead | Critical |
| volume | Today is within 50% of the average of the last 7 batches | Critical |
| quarantine rate | Below 5% | Warning |
| duplicate rate | Below 3% | Warning |
| amount median | Within 3x of the median of the last 7 batches | Warning |

A critical failure stops the DAG between silver and gold. Gold then keeps yesterday, which is
correct data, instead of being rebuilt from a feed that broke overnight.

Every check reads the tables. None takes a count from `refine_silver`, because a check that
trusts the process it checks is not a check.

Measured: a batch of 808 rows against a 3-batch average of 20,200 stops the run, and gold stays
at 59,523 rows.

## The model

`finplat/train.py` fits an XGBoost classifier on the gold features. Two rules hold it honest:

1. **The split is by time.** The oldest 80% of judged rows train the model. The newest 20% test
   it. A random split lets the model learn from Thursday to predict Wednesday.
2. **The cutoff is a label date.** `training_frame(lake, as_of)` drops every transaction whose
   verdict arrived after `as_of`, so a run cannot read a chargeback that has not happened.

Accuracy is not reported. At a 1.5% fraud rate, a model that always answers "not fraud" scores
98.5%. The run reports PR-AUC, and the precision and recall at the best threshold.

MLflow records each run and registers the model. One version carries the alias `production`:

```text
version pr_auc    precision   recall    alias
2       0.9478    0.9665      0.8587    production
1       0.9478    0.9665      0.8587    -
```

The scorer loads `models:/fraud_model@production`, never a file path. A promotion therefore
changes the live model with no deploy, and a rollback is one command.

Read the PR-AUC of 0.95 against the limits in "What this data is not". The generator writes a
clean fraud pattern on purpose, so a high score measures the data and not the model.

## Local ports

| Port | Service |
| ---- | ------- |
| 8095 | Airflow |
| 8096 | MLflow |
| 8097 | The live page |
| 5440 | Postgres |

## CI and the images

Each push to `main` runs three jobs in `.github/workflows/ci.yml`:

1. **test**: lint, format check, and `pytest` against a Postgres service.
2. **terraform**: `terraform fmt` and `terraform validate`, with no AWS account.
3. **image**: builds the two images and pushes them to GitHub Container Registry. This job runs
   only when the other two pass.

Each image gets two tags: the commit SHA and `latest`. The server pins the SHA. Anyone can pull
the images without a login:

- `ghcr.io/syakeerrahman/regulated-finance-data-platform-api`
- `ghcr.io/syakeerrahman/regulated-finance-data-platform-airflow`

## Deploying to a server

The server stack is in `deploy/`. It runs Airflow, the live service, Postgres and MLflow, each
with a memory limit. No port is open to the internet. You reach each service through an SSH
tunnel.

**Status:** these steps are not yet tested on a real server. The containers, the bootstrap, the
backup and the restore were rehearsed on fresh volumes on 2026-09-27. The server preparation in
part 1 is standard Ubuntu setup and was not rehearsed.

### What the server needs

| Item | Value |
| ---- | ----- |
| Operating system | Ubuntu 24.04 |
| Memory | 2.2 GB used by this project, measured. The limits total 4.25 GiB |
| Disk | 5.6 GB of images, plus about 0.5 GB of data. Budget 8 GB |
| Open ports | 22 only |
| Backup bucket | Any S3-compatible bucket: AWS S3 or Cloudflare R2 |

### Part 1: prepare the server

Do these steps once, on a new server.

1. Connect as root: `ssh root@<server ip>`
2. Make a user: `adduser finplat`
3. Give the user sudo: `usermod -aG sudo finplat`
4. On your PC, copy your SSH key: `ssh-copy-id finplat@<server ip>`
5. Connect as the new user: `ssh finplat@<server ip>`
6. In `/etc/ssh/sshd_config`, set `PasswordAuthentication no` and `PermitRootLogin no`.
7. Restart SSH: `sudo systemctl restart ssh`
8. Before you close this session, open a second SSH session to make sure that the key works.
9. Allow SSH through the firewall: `sudo ufw allow OpenSSH`
10. Turn on the firewall: `sudo ufw enable`
11. Install Docker: `curl -fsSL https://get.docker.com | sudo sh`
12. Let the user run Docker: `sudo usermod -aG docker finplat`
13. Disconnect and connect again, so that the new group applies.

### Part 2: start the stack

1. Make the folder:

   ```bash
   sudo mkdir -p /srv/finplat
   sudo chown finplat /srv/finplat
   ```

2. Get the code:

   ```bash
   git clone https://github.com/SyakeerRahman/regulated-finance-data-platform.git /srv/finplat
   ```

3. Go to the deploy folder: `cd /srv/finplat/deploy`
4. Make the settings file: `cp .env.example .env`
5. Make a Postgres password: `openssl rand -hex 24`
6. Open `.env` and fill in these values:
   - `POSTGRES_PASSWORD`: the password from step 5.
   - `IMAGE_TAG`: the commit SHA of the last green CI run on `main`.
   - `BACKUP_*`: the bucket settings. `.env.example` shows the values for S3 and for R2.
7. Make the file private: `chmod 600 .env`
8. Download the images: `docker compose pull`
9. Start the stack: `docker compose up -d`
10. Fill the empty server: `./bootstrap.sh`

The bootstrap takes about 2 minutes. It runs 3 days of the pipeline, trains one model, and
promotes it. Until it finishes, the `api` container restarts in a loop. That is expected: the
service cannot start without data and a model.

11. Make sure that the service answers: `curl -s http://127.0.0.1:8097/api/state`

### Part 3: turn on the nightly backup

1. Run one backup by hand: `./backup.sh`
2. Make sure that the bucket has a new folder under `finplat/`.
3. Open the cron table: `crontab -e`
4. Add this line. It runs the backup at 03:15 each night:

   ```text
   15 3 * * * cd /srv/finplat/deploy && ./backup.sh >> backups/backup.log 2>&1
   ```

The backup saves Postgres (the alerts and the analyst decisions) and MLflow (the model registry
and the model files). It does not save the lake, because the lake regenerates from a fixed seed.
The server keeps 7 days of backups. The bucket keeps the rest.

### Part 4: open the dashboards

The services listen on the server's own address only. From your PC, open an SSH tunnel:

```bash
ssh -L 8095:127.0.0.1:8095 -L 8096:127.0.0.1:8096 -L 8097:127.0.0.1:8097 finplat@<server ip>
```

Then open these addresses on your PC:

| Address | Service |
| ------- | ------- |
| http://localhost:8097 | The dashboard |
| http://localhost:8096 | MLflow |
| http://localhost:8095 | Airflow |

Airflow on the server gives every visitor admin rights. Never expose port 8095 to the internet.

### Deploy a new version

1. Find the commit SHA of the new green CI run on `main`.
2. Go to the deploy folder: `cd /srv/finplat/deploy`
3. Get the latest deploy files: `git pull`
4. In `.env`, set `IMAGE_TAG` to the new SHA.
5. Download the new images: `docker compose pull`
6. Restart with the new images: `docker compose up -d`
7. Remove the old images to free disk space: `docker image prune -f`

To roll back, do the same steps with the previous SHA.

### Restore a backup

The backups are named by the time they were made, for example `2026-09-27T0315Z`.

**After a bad deploy, on a working server:**

1. Go to the deploy folder: `cd /srv/finplat/deploy`
2. Restore: `./restore.sh <stamp>`

**After a lost server:**

1. Do part 1 and part 2, steps 1 to 9, on the new server. Do not run `bootstrap.sh` yet.
2. Restore from the bucket: `./restore.sh <stamp>`
3. Rebuild the lake: `./bootstrap.sh`

The bootstrap sees the restored model and does not train a new one.

A restore replaces the alerts, the decisions and the model registry with the copy in the backup.
Data written after the backup is lost.

### Not done yet

These steps need a domain and a Cloudflare account:

- **Cloudflare Tunnel and Cloudflare Access.** They give the dashboard a public HTTPS address
  behind an email login. No port opens on the server.
- **Deploy on merge.** CI will connect to the server and run the "Deploy a new version" steps.
- **An alert when the platform stops.**

## The live path

`api/main.py` invents a transaction, scores it, and pushes it to the browser over server-sent
events. Three rules hold it to the batch path:

1. **One feature definition.** `finplat/features.py` holds the arithmetic. `build_gold` calls
   `frame_features` for a whole table and the scorer calls `row_features` for one transaction.
   A test replays 3,000 rows through both and asserts every column agrees.
2. **The history is warmed from the lake.** A scorer that starts cold treats every account as
   new, so `amount_vs_account` is 1.0 on every row and the model scores a distribution it never
   trained on.
3. **The threshold comes from the model.** Training picks it from the precision-recall curve and
   logs it. The service reads it from the registry. A number typed into the service drifts away
   from the model at the first retrain.

Measured at 50 transactions each second: 0.93% of rows raise an alert, against a 1.5% fraud
rate.

## The dashboard

Seven tabs, at http://localhost:8097.

| Tab | Shows |
| --- | ----- |
| Live | Start and rate controls, a counter, a volume chart, and the rows as they arrive |
| Alerts | One card for each alert: its reason, its policy rule, the AI analyst, and the two decision buttons |
| Model | The live MLflow version, every registered version, and why accuracy is not reported |
| Pipeline | The data quality grid, one column for each batch |
| Data | The lake, layer by layer, down to single rows |
| Ask AI | A chat that answers questions from the live data, the policy, and the AI agreement rate |
| Account | One account's history, its features, and the alerts raised against it |

Three rules the charts follow:

1. **One y-axis.** Alerts are a subset of transactions, so they share the scale. A second axis
   would draw a 1% rate at the same height as a 50% one.
2. **A legend is always present for two series.** Identity never rests on colour alone.
3. **The quality grid carries a glyph, not only a colour.** Status good and status critical
   measure 4.1 apart under deuteranopia, so the tick and the cross carry the meaning.

The palette is the validated dark instance: series blue `#3987e5` and orange `#d95926` on
surface `#1a1a19`, which measure CVD Delta E 26.8 and normal-vision 31.8 apart.

## Alerts, and the loop that closes

An alert is a row in Postgres, not in Delta and not in Redis. It has to survive a restart, be
fetched by its own id, and change state when somebody presses a button.

Each alert carries its reason. SHAP reports how much each feature moved the score, and
`finplat/explain.py` turns the top three into a sentence:

```text
#8  ACC39002  MYR 153.14  SG/online/electronics
  score 1.000 (threshold 0.902), model v3, status open
  WHY: 18x this account's normal spend, a purchase outside Malaysia, and a category that
       resells easily.
  contributions: {'amount_vs_account': 4.36, 'is_abroad': 2.36, 'is_risky_category': 1.56}
```

SHAP walks every tree, so it runs for an alert and not for the 99% of rows that pass.

An analyst then confirms the alert or calls it a false positive. That decision is the same kind
of fact as a chargeback, so it goes into `silver/labels` with `label_source = analyst` and the
next retrain reads it. Measured end to end: two decisions, and a retrain run today sees exactly
those two rows, because every other label is still inside its dispute window.

## The AI layer

Stage G adds a language model. It explains, investigates and answers questions. It never decides.
The reasons are in `brain/decisions/2026-10-03-the-llm-narrates-and-code-cites.md`.

| Part | What it does | Where |
| ---- | ------------ | ----- |
| Policy citation | Code picks the rule of `FP-2026` that the alert breaks, when the alert is raised | `finplat/policy.py` |
| AI analyst | Two sentences, a suggestion (likely fraud, likely false positive or unsure) and one check | `finplat/assistant.py`, `narrate` |
| Past cases | A search by meaning over earlier alerts, with each outcome as it was known then | `finplat/cases.py` |
| Case note | An agent drafts a note from evidence that code reads. The analyst approves it or returns it | `finplat/case_flow.py` |
| Ask AI | A chat with twelve read-only tools over Postgres, the lake and MLflow | `finplat/assistant.py`, `ask` |
| Agreement | How often the suggestion matched the analyst decision | `Store.agreement` |
| MCP server | The same twelve tools for any MCP client, for example Claude Desktop | `finplat/mcp_server.py` |

Six rules hold the layer in place:

1. **Code cites the rule.** A regulator asks which rule an alert broke. The answer must be the same
   on every replay, so the model receives the rule and cannot change it.
2. **The tools only read.** A test fails if a tool name starts with a write verb. The rule holds
   for every client: Ask AI and the MCP server serve one list through one dispatch.
3. **A suggestion is never a label.** It is stored in `ai_suggestion`, beside the analyst status.
   `export_decisions` reads only the status. A test checks this with a suggestion and a decision
   that disagree.
4. **The model is optional.** With no key, or when a call fails, each alert keeps its SHAP reason
   and its policy rule. The dashboard says that the AI is off.
5. **Each answer keeps its prompt version.** The version is a hash of the prompt file in
   `finplat/prompts/`, so an edit changes it and nobody has to remember to.
6. **A draft is not a case note.** The agent writes a draft. Only the analyst's approval saves it.

### Past cases

Each alert with an AI summary is a case. `finplat/cases.py` embeds its text into the
`case_vectors` table in Postgres, with pgvector. The text holds the payment, the top reason, the
policy rules, the summary and the case note.

Each case has an outcome, a source and the time the outcome became known:

| Source | Known at | Note |
| ------ | -------- | ---- |
| `analyst` | The time of the decision | Wins over a label |
| `label` | `labelled_at` in `silver/labels` | A chargeback or a closed dispute window, 30 to 90 days after the payment |
| `simulated` | 1 hour after the alert | The true answer, for the demo only. It is never written to `alerts.status`, so it never becomes a training label |
| none | - | Pending. Not evidence either way |

A search for an alert shows each past case as it was when that alert was raised. It does not
return a case raised later. It shows an outcome that became known later as pending. This is the
same rule as `amount_vs_account`: a feature must not see the future.

The search is exact, over every case, by cosine distance. An HNSW index comes only when a
measurement shows a need for it.

```text
docker compose exec api python -m finplat.cases index
docker compose exec api python -m finplat.cases simulate --count 200
```

`index` embeds new cases and cases with new text, then sets every outcome. A second run sends no
request. The API also indexes a case after each decision, summary or case note.

`simulate` exists because no label is known yet. The lake starts on 2026-09-24, and labels arrive
30 to 90 days late. The dashboard marks every simulated outcome as "simulated, demo".

### The case note waits for the analyst

The case note is a LangGraph workflow, in `finplat/case_flow.py`:

```text
gather_evidence -> retrieve_cases -> draft_note -> analyst_review
analyst_review: approve -> save_note
analyst_review: return  -> draft_note
```

1. Code reads the alert, the account, the similar alerts and the past cases.
2. The model writes a draft. It names each past case that it uses by its id, for example #567.
3. The run stops at `analyst_review` and waits.
4. The analyst approves the draft, or returns it with a comment. A returned draft comes back as a
   new draft that answers the comment.
5. After 3 returns, the analyst writes the note by hand.

The LangGraph Postgres checkpointer saves the state of each run, so a draft waits through a
restart of the API. The approved note records the analyst's login from Cloudflare Access, when
there is one.

### Use the tools from Claude Desktop

`finplat/mcp_server.py` serves the Ask AI tools over MCP, on stdio. It opens no port. The client
starts the server inside the API container, so the server reads the same Postgres, lake and MLflow.

| MCP part | Content |
|---|---|
| Tools | The twelve Ask AI tools, each marked read-only |
| Resources | `finplat://policy/rules`, and `finplat://catalog/<table>` for each lake table |
| Prompt | `investigate_alert(alert_id)`: five steps that end without a decision |

To connect Claude Desktop, start the local stack, then add this to
`%APPDATA%\Claude\claude_desktop_config.json` and restart Claude Desktop:

```json
{
  "mcpServers": {
    "finplat": {
      "command": "docker",
      "args": ["exec", "-i", "finplat-api", "python", "-m", "finplat.mcp_server"]
    }
  }
}
```

For Claude Code: `claude mcp add finplat -- docker exec -i finplat-api python -m finplat.mcp_server`.

Do not use `docker compose exec`. An MCP client starts the server with only a few environment
variables, and the Docker CLI then cannot find its compose plugin. That is why the API container
has the fixed name `finplat-api`.

Example questions: "Why did yesterday's batch fail, and which alerts came from it?", "What does
`amount_vs_account` mean?", "Which accounts had the most alerts this week?"

### How good the AI suggestion is

The data is synthetic, so the true answer of every alert can be rebuilt from its id.
`finplat/ai_eval.py` asks the AI about a sample of alerts, then joins the truth. It logs the
result to the `ai-analyst` experiment in MLflow, and the Ask AI tab shows the newest one.

```text
docker compose exec api python -m finplat.ai_eval --alerts 100 --seed 23
```

Measured on 2026-10-03, on 100 alerts each:

| Prompt | Right, when it answers | Always saying fraud | False alarms it spots | Fraud it calls a false alarm |
| ------ | ---------------------- | ------------------- | --------------------- | ---------------------------- |
| `3c0a0ee7` | 48% | 46% | 0% | 0% |
| `473f87b9` | 92% | 38% | 82% | 13% |

The first prompt called almost every alert fraud, so it added nothing to the score. Alerts just
above the threshold are mostly honest: 1 in 8 is fraud below 0.99, and 19 in 20 above 0.999. The
second prompt tells the model how to read the score. It was graded on a different sample from
the one its numbers came from.

The last column is the reason a person decides. An analyst who followed the AI alone would close
13% of real fraud as false alarms.

`--compare` grades the same alerts twice, without and with their past cases. It stores no answer,
and it logs to the `ai-analyst-retrieval` experiment, so the Ask AI tab keeps the live grade.

```text
docker compose exec api python -m finplat.ai_eval --compare --alerts 50 --seed 41
```

Measured on 2026-10-04, on 50 alerts, with prompt `0043499e`:

| Past cases | Right, when it answers | False alarms it spots | Fraud precision | Decided cases seen |
| ---------- | ---------------------- | --------------------- | --------------- | ------------------ |
| Without | 90.0% | 80% | 92.3% | 0 |
| With | 87.8% | 80% | 85.7% | 2.08 for each alert |

Retrieval did not improve the suggestion. The difference is 1 alert, which is noise at this
sample size. The model saw about 2 decided cases for each alert, and the prompt lets past cases
count only when 3 or more agree. The case note still cites past cases, so the analyst can check
them.

The model writes the narrative only when an analyst opens the alert, and the answer is stored.
At 1 payment each second, about 860 alerts arrive each day, and most are never opened.

To turn the layer on, add the key to `.env`:

```text
LLM_API_KEY=sk-...
```

`DEEPSEEK_API_KEY` is also accepted. To use another provider, also set `LLM_BASE_URL` and
`LLM_MODEL`. The provider must accept the OpenAI chat format with tool calls. For example, an
OpenRouter key needs these two lines:

```text
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=deepseek/deepseek-chat
```

The dashboard has no login until Cloudflare Access is in front of it, and each AI call spends
the owner's credit. `LLM_DAILY_CALLS` caps the model calls for each UTC day. The default is 500.
After that, the AI endpoints answer 429 until 00:00 UTC. One question can use up to 6 calls.

The case search turns text into vectors with `EMBED_MODEL`, by default
`openai/text-embedding-3-small`, which returns 1536 numbers. It uses the chat model's base URL
and key unless `EMBED_BASE_URL` and `EMBED_API_KEY` are set. DeepSeek has no embeddings, so with
DeepSeek as the chat model, set both for another provider. `EMBED_DIM` must match the model and
must be 2000 or less, the pgvector index limit. Each embedding request counts in
`LLM_DAILY_CALLS`.

DeepSeek processes requests on servers in China. The data here is synthetic. A real card issuer
must not send card data to a provider outside its approved jurisdictions.

The provider receives this case data: the alert, the account history, the similar alerts, the
past cases, and the text of each case for its embedding. The data is synthetic. A real bank would
use a provider in an approved jurisdiction, or a local embedding model.

## The live feed writes to the lake in batches

Each Delta write makes a parquet file and a log entry. One write per transaction would leave
86,400 files a day and a table nothing can open. So the scorer buffers and flushes every 2,000
rows or 60 seconds, whichever comes first.

The live feed owns its own partition, `live-<date>`. The daily DAG owns the partition named
after the date, and two writers on one partition is how a replace deletes the other writer's
rows.

## The Lambda scores without XGBoost

A zip Lambda has 50 MB zipped and 250 MB unzipped. XGBoost and its shared library spend most of
that on their own, and importing them costs seconds on a cold start.

A gradient boosted tree is only a list of if-then branches. So `finplat/export.py` writes the
300 trees as JSON, and `lambda_fn/scorer.py` walks them in about 60 lines of standard library.

| | Value |
| --- | ----- |
| Package | 115 KB |
| Third-party dependencies | none |
| Cold import | 64 ms |
| One score | 0.9 ms |

Two implementations of one model is the shape of a silent production bug, so
`tests/test_lambda.py` scores the same 295 rows through the Lambda scorer and through XGBoost,
and asserts they agree to 1e-6.

The Lambda holds no state. The caller sends the account history, because reading the lake on
every request would cost more than the score is worth.

Deployment is in [terraform/README.md](terraform/README.md). It creates one Lambda, one role,
one log group and one Function URL. There is no API Gateway, no ECR and no S3 bucket, so nothing
starts to charge after 12 months.
