"""The live path: invent a transaction, score it, push it to the browser.

    uv run uvicorn api.main:app --port 8097 --reload

Stage E replaces the in-memory state with Redis and the alert list with Postgres, and puts a
React app in front. The shape stays: one loop in, one score, one stream out.
"""

import asyncio
import contextlib
import json
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import SimpleQueue

import pandas as pd
from deltalake import DeltaTable
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from finplat import ai_eval, assistant, lake_browser, model_report, ops, policy, prompts
from finplat.alerts import DECISIONS, Store, export_decisions
from finplat.embed import Embedder
from finplat.explain import Explainer, sentence
from finplat.features import FEATURE_COLUMNS, AccountHistory, row_features
from finplat.feed import Pool
from finplat.live_stats import LiveStats
from finplat.llm import LLM, Budget, BudgetExceeded, LLMError
from finplat.pipeline import GOLD, LIVE_PREFIX, SILVER, append_bronze
from finplat.quality import history as quality_history
from finplat.registry import REGISTERED_MODEL, live_version, load_production, production_threshold, promote
from finplat.settings import get_settings

RECENT = 200
# Live feature rows kept for the drift check. About 7 minutes at 50 a second.
DRIFT_SAMPLE = 20_000
DEFAULT_RATE = 1.0

# Never one row at a time. Each Delta write makes a parquet file and a log entry, so a row for
# every transaction would leave 86,400 files a day and a table nothing can open.
FLUSH_ROWS = 2_000
FLUSH_SECONDS = 60

STATIC = Path(__file__).parent / "static"


def _python(values: dict) -> dict:
    """numpy booleans and integers do not survive json.dumps, and this row goes to the stream."""
    return {key: value.item() if hasattr(value, "item") else value for key, value in values.items()}


class Engine:
    """One generator loop, one model, and the last few hundred results."""

    def __init__(self) -> None:
        settings = get_settings()
        self.lake = settings.lake_uri
        self.tracking_uri = settings.mlflow_tracking_uri
        self.airflow_url = settings.airflow_url
        self.model = load_production(settings.mlflow_tracking_uri)
        self.explainer = Explainer(self.model, FEATURE_COLUMNS)
        self.store = Store(settings.postgres_dsn)
        self.store.migrate()
        # One budget for both: every request spends the same owner's credit.
        budget = Budget(settings.llm_daily_calls)
        self.llm = LLM(settings.llm_base_url, settings.llm_model, settings.llm_api_key, budget)
        # `or`, not a default: the server compose file passes an unset variable as an empty string.
        self.embedder = Embedder(
            settings.embed_base_url or settings.llm_base_url,
            settings.embed_model,
            settings.embed_dim,
            settings.embed_api_key or settings.llm_api_key,
            budget,
        )
        self.model_version = live_version(settings.mlflow_tracking_uri)
        self.threshold = production_threshold(settings.mlflow_tracking_uri)
        self.history = AccountHistory()
        self.warmed = self._warm(settings.lake_uri)
        self.recent: deque[dict] = deque(maxlen=RECENT)
        self.alerts: deque[dict] = deque(maxlen=RECENT)
        self.listeners: set[asyncio.Queue] = set()
        self.rate = DEFAULT_RATE
        self.scored = 0
        self.sample: deque[dict] = deque(maxlen=DRIFT_SAMPLE)
        self.stats = LiveStats(time.time())
        self._buffer: list[dict] = []
        self._flushed_at = time.monotonic()
        self.written = 0
        self._task: asyncio.Task | None = None
        # The generator is the same one the batch pipeline uses, so the live data and the training
        # data come from one place.
        self.pool = Pool(settings.seed, settings.accounts)
        self.stats.note(
            "service",
            f"Started. Model v{self.model_version} loaded, {self.warmed:,} accounts warmed from silver",
            time.time(),
        )

    def _warm(self, lake: str) -> int:
        """Load the account history the batch pipeline already built. Redis holds this in stage E."""
        silver = DeltaTable(f"{lake}/{SILVER}").to_pandas(columns=["account_id", "amount"])
        return self.history.warm(silver)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._loop())
            self.stats.note("feed", f"Feed started at {self.rate:g} payments a second", time.time())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            self.stats.note("feed", "Feed stopped", time.time(), level="warning")
        # Whatever is still in the buffer belongs in the lake, not in a stopped process.
        await self._maybe_flush(force=True)

    async def _loop(self) -> None:
        while True:
            transaction = self._take()
            started = time.perf_counter()
            result = self.score(transaction)
            # Features, the model and SHAP when it alerts. Not the stream or the lake write.
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.publish(result)
            self._buffer.append(transaction)
            await self._maybe_flush()
            await asyncio.sleep(1 / self.rate)

    async def _maybe_flush(self, force: bool = False) -> None:
        due = len(self._buffer) >= FLUSH_ROWS or (time.monotonic() - self._flushed_at) >= FLUSH_SECONDS
        if not self._buffer or not (due or force):
            return

        rows, self._buffer = self._buffer, []
        self._flushed_at = time.monotonic()
        # Its own partition. The daily DAG owns the partition named after the date, and two writers
        # on one partition is how a replace deletes the other writer's rows.
        batch_id = LIVE_PREFIX + pd.Timestamp.now(tz="UTC").date().isoformat()
        # A Delta write takes seconds. On the event loop it would stall the feed and every
        # browser watching it.
        started = time.perf_counter()
        try:
            written = await asyncio.to_thread(append_bronze, self.lake, pd.DataFrame(rows), batch_id)
        except Exception as error:
            self.stats.note(
                "lake", f"Write of {len(rows):,} rows to bronze failed: {error}", time.time(), level="error"
            )
            raise
        self.written += written
        self.stats.record_write(written, time.time())
        self.stats.note(
            "lake",
            f"Wrote {written:,} rows to bronze ({batch_id}) in {time.perf_counter() - started:.1f} s",
            time.time(),
        )

    def _take(self) -> dict:
        transaction = self.pool.take()
        # The pool holds whole days. Stamp the real clock on each row so the feed reads as live.
        transaction["ts"] = pd.Timestamp.now(tz="UTC")
        return transaction

    def score(self, transaction: dict) -> dict:
        account = transaction["account_id"]
        count, mean = self.history.prior(account)
        features = row_features(transaction, count, mean)
        matrix = pd.DataFrame([features])[FEATURE_COLUMNS].astype("float64")
        # predict_proba, not predict. The class label would only ever say 0 or 1.
        score = float(self.model.predict_proba(matrix)[0, 1])
        self.history.add(account, float(transaction["amount"]))
        self.sample.append({**features, "score": score})
        self.scored += 1

        alert = score >= self.threshold
        # SHAP walks every tree, so it runs for an alert and not for the 99% that pass.
        reasons = self.explainer.reasons(features) if alert else []
        contributions = {reason.feature: round(reason.contribution, 4) for reason in reasons}
        # The rule is cited here, by code, when the alert is raised. The language model only ever
        # explains a rule that is already chosen.
        facts = {**features, "country": transaction["country"]}
        rules = [rule.rule_id for rule in policy.cite(facts, contributions)] if alert else []

        return {
            "transaction_id": transaction["transaction_id"],
            "account_id": account,
            "amount": round(float(transaction["amount"]), 2),
            "country": transaction["country"],
            "channel": transaction["channel"],
            "merchant_category": transaction["merchant_category"],
            "ts": pd.Timestamp(transaction["ts"]).isoformat(),
            "amount_vs_account": round(features["amount_vs_account"], 2),
            "prior_transactions": features["prior_transactions"],
            "score": round(score, 4),
            "alert": alert,
            "threshold": round(self.threshold, 4),
            "model_version": self.model_version,
            "reason": sentence(reasons),
            "contributions": contributions,
            "policy_rules": rules,
            "features": _python(features) if alert else None,
        }

    async def reload_model(self) -> None:
        """Load whichever version the production alias points at. Called after a promotion."""

        def load():
            model = load_production(self.tracking_uri)
            return (
                model,
                Explainer(model, FEATURE_COLUMNS),
                live_version(self.tracking_uri),
                production_threshold(self.tracking_uri),
            )

        loaded = await asyncio.to_thread(load)
        # Assigned here, on the event loop, where the feed also runs. No payment can be scored
        # between two of these lines, so none is scored by one version against another's threshold.
        self.model, self.explainer, self.model_version, self.threshold = loaded
        # The drift sample describes the old model's scores. Mixing the two would hide a change.
        self.sample.clear()
        self.stats.note("model", f"Loaded model v{self.model_version}, threshold {self.threshold:.3f}", time.time())

    def publish(self, result: dict) -> None:
        self.recent.appendleft(result)
        self.stats.add(result, time.time())
        if result["alert"]:
            self.alerts.appendleft(result)
            self.store.raise_alert(result, result["reason"], result["contributions"])
        for queue in list(self.listeners):
            # A browser that cannot keep up loses rows rather than stalling the whole loop.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(result)


engine: Engine | None = None


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    global engine
    engine = Engine()
    yield
    await engine.stop()


app = FastAPI(title="finplat live", lifespan=lifespan)


@app.get("/api/state")
def state() -> dict:
    return {
        "running": engine.running,
        "rate": engine.rate,
        "scored": engine.scored,
        "alerts": len(engine.alerts),
        "accounts_warmed": engine.warmed,
        "written_to_lake": engine.written,
        "buffered": len(engine._buffer),
        "model_version": engine.model_version,
        "threshold": round(engine.threshold, 4),
        "recent": list(engine.recent)[:50],
        "open_alerts": engine.store.counts().get("open", 0),
        "live": engine.stats.snapshot(time.time()),
    }


@app.post("/api/start")
async def start(rate: float = DEFAULT_RATE) -> dict:
    # async, not sync: FastAPI runs a sync endpoint in a worker thread, and asyncio.create_task
    # needs the loop that is running in the main thread.
    rate = max(0.1, min(rate, 50.0))
    if engine.running and rate != engine.rate:
        engine.stats.note("feed", f"Rate changed to {rate:g} payments a second", time.time())
    engine.rate = rate
    engine.start()
    return {"running": True, "rate": engine.rate}


@app.post("/api/stop")
async def stop() -> dict:
    await engine.stop()
    return {"running": False}


@app.get("/api/stream")
async def stream() -> StreamingResponse:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    engine.listeners.add(queue)

    async def events():
        try:
            while True:
                try:
                    result = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(result)}\n\n"
                except TimeoutError:
                    # A proxy closes an idle connection, so say something every 15 seconds.
                    yield ": keep-alive\n\n"
        finally:
            engine.listeners.discard(queue)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/alerts")
def alerts(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    country: str | None = None,
    reason: str | None = None,
    q: str | None = None,
    hours: int | None = None,
) -> dict:
    since = datetime.now(UTC) - timedelta(hours=hours) if hours else None
    rows, total = engine.store.search(
        since=since,
        status=status,
        country=country,
        reason=reason,
        text=q,
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
    )
    return {"alerts": [_with_rules(row) for row in rows], "total": total, "counts": engine.store.counts()}


@app.get("/api/alerts/summary")
def alerts_summary() -> dict:
    return engine.store.summary(datetime.now(UTC))


class Decisions(BaseModel):
    alert_ids: list[int]
    status: str


@app.post("/api/alerts/decisions")
def decide_many(body: Decisions) -> dict:
    """The same decision on every selected alert."""
    if body.status not in DECISIONS:
        raise HTTPException(400, f"status must be one of {DECISIONS}")
    return {"changed": engine.store.decide_many(body.alert_ids, body.status)}


@app.post("/api/alerts/{alert_id}/decision")
def decide(alert_id: int, status: str) -> dict:
    """An analyst confirms fraud or calls it a false positive.

    The decision is a label. `POST /api/labels/export` writes every decision into the label
    table, and the next retrain learns from them.
    """
    if status not in DECISIONS:
        raise HTTPException(400, f"status must be one of {DECISIONS}")
    row = engine.store.decide(alert_id, status)
    if row is None:
        raise HTTPException(404, f"no alert {alert_id}")
    return row


@app.post("/api/labels/export")
def export_labels() -> dict:
    return {"written": export_decisions(engine.store, engine.lake)}


# --- stage G: the AI layer -----------------------------------------------------------------------
# Sync endpoints on purpose. A model call takes seconds, and FastAPI runs a sync endpoint in a
# worker thread, so the feed on the event loop never waits for one.


def _with_rules(alert: dict) -> dict:
    """An alert raised before stage G has no stored rule. Cite one now, from the fields it has."""
    if not alert.get("policy_rules"):
        alert["policy_rules"] = [rule.rule_id for rule in assistant.rules_for(alert)]
    return alert


def _alert_or_404(alert_id: int) -> dict:
    alert = engine.store.get(alert_id)
    if alert is None:
        raise HTTPException(404, f"no alert {alert_id}")
    return _with_rules(alert)


def _llm_failure(error: LLMError) -> HTTPException:
    # 429 says "come back later", which is true of a spent budget and not of a broken provider.
    return HTTPException(429 if isinstance(error, BudgetExceeded) else 502, str(error))


def _require_llm() -> None:
    if not engine.llm.enabled:
        raise HTTPException(503, "The AI is off. Set LLM_API_KEY or DEEPSEEK_API_KEY in .env and restart the service.")


def _toolbox() -> assistant.Toolbox:
    def model_info() -> dict:
        versions = cached("versions", 30, lambda: model_report.version_rows(engine.tracking_uri))
        return {"live_version": engine.model_version, "threshold": engine.threshold, "versions": versions}

    return assistant.Toolbox(engine.store, engine.lake, model_info)


@app.get("/api/ai")
def ai_status() -> dict:
    return {
        "enabled": engine.llm.enabled,
        "model": engine.llm.model,
        "budget": engine.llm.budget.snapshot(),
        "base_url": engine.llm.base_url,
        "prompts": prompts.versions(),
        "agreement": engine.store.agreement(),
        "embeddings": {
            "enabled": engine.embedder.enabled,
            "model": engine.embedder.model,
            "dim": engine.embedder.dim,
            "base_url": engine.embedder.base_url,
        },
    }


@app.get("/api/ai/evaluation")
def ai_evaluation() -> dict:
    """The newest score of the AI analyst against the true answers. `python -m finplat.ai_eval` writes it."""
    try:
        return {"evaluation": cached("ai-evaluation", 300, lambda: ai_eval.latest(engine.tracking_uri))}
    except Exception as error:  # noqa: BLE001 - MLflow down is a missing panel, not a broken tab
        return {"evaluation": None, "error": str(error)}


@app.get("/api/policy")
def policy_rules() -> dict:
    return {"name": policy.POLICY_NAME, "rules": [rule.public() for rule in policy.RULES]}


@app.get("/api/alerts/{alert_id}")
def get_alert(alert_id: int) -> dict:
    return _alert_or_404(alert_id)


@app.get("/api/alerts/{alert_id}/similar")
def similar_alerts(alert_id: int, limit: int = 5) -> dict:
    _alert_or_404(alert_id)
    return {"similar": engine.store.similar(alert_id, max(1, min(limit, 20)))}


@app.post("/api/alerts/{alert_id}/narrative")
def alert_narrative(alert_id: int, refresh: bool = False) -> dict:
    """Two sentences and a suggestion from the language model. Kept, so a second look costs nothing."""
    alert = _alert_or_404(alert_id)
    if alert["ai_summary"] and not refresh:
        return alert
    _require_llm()
    try:
        narrative = assistant.narrate(engine.llm, alert, engine.store.for_account(alert["account_id"]))
    except LLMError as error:
        raise _llm_failure(error) from error
    saved = engine.store.save_narrative(alert_id, narrative, engine.llm.model, prompts.load("narrate").version)
    return _with_rules({**alert, **saved})


@app.post("/api/alerts/{alert_id}/case-note")
def alert_case_note(alert_id: int, refresh: bool = False) -> dict:
    """The investigation agent reads the alert, the account and the policy, then writes a case note."""
    alert = _alert_or_404(alert_id)
    if alert["case_note"] and not refresh:
        return {"alert": alert, "tools": []}
    _require_llm()
    try:
        note = assistant.case_note(engine.llm, _toolbox(), alert_id)
    except LLMError as error:
        raise _llm_failure(error) from error
    saved = engine.store.save_case_note(alert_id, note["answer"], note["model"], note["prompt_version"])
    return {"alert": _with_rules({**alert, **saved}), "tools": note["tools"]}


class Message(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=2_000)


class Question(BaseModel):
    # The browser keeps the conversation and sends it whole. A cap keeps one tab from sending a
    # conversation long enough to cost real money on every question.
    messages: list[Message] = Field(min_length=1, max_length=20)


def _question(body: Question) -> list[dict]:
    if body.messages[-1].role != "user":
        raise HTTPException(400, "the last message must be the question")
    _require_llm()
    return [message.model_dump() for message in body.messages]


@app.post("/api/ask")
def ask(body: Question) -> dict:
    messages = _question(body)
    try:
        return assistant.ask(engine.llm, _toolbox(), messages)
    except LLMError as error:
        raise _llm_failure(error) from error


@app.post("/api/ask/stream")
def ask_stream(body: Question) -> StreamingResponse:
    """The same answer as /api/ask, sent as it is made.

    A question took 10 to 19 seconds with nothing on screen. Now the page shows each tool as the
    agent calls it, then the answer as it is written. Server-sent events over a POST, because the
    question is a body, and EventSource can only send a GET.
    """
    messages = _question(body)
    events: SimpleQueue = SimpleQueue()

    def work() -> None:
        try:
            answer = assistant.ask(engine.llm, _toolbox(), messages, events.put)
            events.put({"type": "done", **answer})
        except LLMError as error:
            events.put({"type": "error", "status": _llm_failure(error).status_code, "detail": str(error)})
        except Exception as error:  # noqa: BLE001 - the reader must hear that the answer stopped
            events.put({"type": "error", "status": 500, "detail": f"the answer stopped: {error}"})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def send():
        while (event := events.get()) is not None:
            yield f"data: {json.dumps(event, default=str)}\n\n"

    # no-transform and X-Accel-Buffering stop a proxy from holding the stream until it ends.
    headers = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
    return StreamingResponse(send(), media_type="text/event-stream", headers=headers)


_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, seconds: float, read):
    """The last answer while it is fresh. Every open browser polls, and the lake and Airflow
    should not be asked once for each of them."""
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < seconds:
        return hit[1]
    value = read()
    _cache[key] = (now, value)
    return value


def _checks() -> dict:
    def postgres() -> str:
        with engine.store.connect() as connection:
            connection.execute("select 1")
        return "Alerts store answering"

    def lake() -> str:
        return f"silver at version {DeltaTable(f'{engine.lake}/{SILVER}').version()}"

    return {
        "Scoring service": lambda: f"Model v{engine.model_version}, threshold {engine.threshold:.3f}",
        "Postgres": postgres,
        "Delta lake": lake,
        "MLflow": ops.mlflow_check(engine.tracking_uri),
        "Airflow": ops.airflow_check(engine.airflow_url),
    }


@app.get("/api/pipeline")
def pipeline() -> dict:
    """Everything the Pipeline tab shows that the live counts in /api/state do not."""
    return {
        "lake": cached("lake", 30, lambda: ops.lake_tables(engine.lake)),
        "airflow": cached("airflow", 10, lambda: ops.airflow_jobs(engine.airflow_url)),
        "health": cached("health", 10, lambda: ops.health(_checks())),
        "feed": {
            "running": engine.running,
            "rate": engine.rate,
            "listeners": len(engine.listeners),
            "buffered": len(engine._buffer),
            "written": engine.written,
        },
    }


@app.post("/api/pipeline/dags/{dag_id}/trigger")
def trigger_dag(dag_id: str) -> dict:
    try:
        answer = ops.trigger(engine.airflow_url, dag_id)
    except Exception as error:
        raise HTTPException(502, f"Airflow did not start the run: {error}") from error
    _cache.pop("airflow", None)
    engine.stats.note("airflow", f"Triggered {dag_id} ({answer['dag_run_id']})", time.time())
    return answer


@app.get("/api/lake/tables")
def lake_tables() -> dict:
    """Each table's size and state, and the batches of the partitioned ones."""

    def read() -> dict:
        tables = ops.lake_tables(engine.lake)
        return {
            "tables": tables,
            "batches": {
                row["table"]: lake_browser.batches(engine.lake, row["table"]) for row in tables if row["exists"]
            },
        }

    return cached("lake-tables", 15, read)


@app.get("/api/lake/rows")
def lake_rows(table: str, batch: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    if table not in ops.LAKE_TABLES:
        raise HTTPException(404, f"no table {table}")
    return lake_browser.page(engine.lake, table, batch=batch, text=q, limit=limit, offset=offset)


@app.get("/api/lake/trace/{transaction_id}")
def lake_trace(transaction_id: str) -> dict:
    """One transaction in every layer, and the alert it raised, if any."""
    with engine.store.connect() as connection:
        alert = connection.execute("select * from alerts where transaction_id = %s", (transaction_id,)).fetchone()
    return {**lake_browser.trace(engine.lake, transaction_id), "alert": alert}


@app.get("/api/model")
def model() -> dict:
    return {
        "name": REGISTERED_MODEL,
        "live": engine.model_version,
        "threshold": engine.threshold,
        "type": type(engine.model).__name__,
        "versions": cached("versions", 30, lambda: model_report.version_rows(engine.tracking_uri)),
        "importance": {
            "global": model_report.importance(engine.model),
            "local": engine.store.reason_weights(),
        },
    }


def _version(version: str) -> dict:
    rows = cached("versions", 30, lambda: model_report.version_rows(engine.tracking_uri))
    row = next((row for row in rows if row["version"] == version), None)
    if row is None:
        raise HTTPException(404, f"no version {version}")
    return row


@app.get("/api/model/evaluation")
def model_evaluation(version: str) -> dict:
    """Curves for one version. Recomputing an old version reads the lake and loads the model, a
    few seconds, so the answer is kept for ten minutes."""
    row = _version(version)
    return cached(f"evaluation-{version}", 600, lambda: model_report.evaluation(engine.tracking_uri, engine.lake, row))


@app.get("/api/model/drift")
def model_drift() -> dict:
    """The live stream against the data the production model was trained on."""
    version = engine.model_version
    row = _version(version)
    reference = cached(
        f"reference-{version}", 3600, lambda: model_report.reference(engine.model, engine.lake, row["label_cutoff"])
    )
    return {"version": version, **model_report.drift(reference, list(engine.sample))}


@app.post("/api/model/versions/{version}/promote")
async def promote_version(version: str) -> dict:
    """Point production at another version, and load it into the running scorer."""
    _version(version)
    await asyncio.to_thread(promote, engine.tracking_uri, version)
    await engine.reload_model()
    _cache.pop("versions", None)
    return {"live": engine.model_version, "threshold": engine.threshold}


@app.get("/api/quality")
def quality(days: int = 30) -> dict:
    """The check grid. One row for each check on each batch."""
    frame = quality_history(engine.lake, days=days)
    return {
        "batches": sorted(frame["batch_id"].unique().tolist()),
        "checks": sorted(frame["check"].unique().tolist()),
        "results": frame.assign(checked_at=frame["checked_at"].astype(str)).to_dict("records"),
    }


@app.get("/api/accounts/{account_id}")
def account(account_id: str, limit: int = 20) -> dict:
    """One account: what it did, and what was raised against it.

    Gold is not partitioned by account, so this scans. That is honest at this size and wrong at
    a real one, where the account history would live in Redis or an indexed table.
    """
    frame = DeltaTable(f"{engine.lake}/{GOLD}").to_pandas()
    rows = frame[frame["account_id"] == account_id].sort_values("ts", ascending=False)
    if rows.empty:
        raise HTTPException(404, f"no transactions for {account_id}")

    recent = rows.head(limit).assign(ts=lambda f: f["ts"].astype(str))
    with engine.store.connect() as connection:
        alerts = connection.execute(
            "select * from alerts where account_id = %s order by created_at desc limit %s",
            (account_id, limit),
        ).fetchall()

    return {
        "account_id": account_id,
        "transactions": len(rows),
        "mean_amount": round(float(rows["amount"].mean()), 2),
        "max_vs_account": round(float(rows["amount_vs_account"].max()), 2),
        "abroad_share": round(float(rows["is_abroad"].mean()), 4),
        "recent": recent.to_dict("records"),
        "alerts": alerts,
    }


# The React build writes here. It is a build artifact, so a fresh clone has no copy of it and
# the service must still start: a message is better than a crash on import. The branch matters,
# because a route registered for "/" wins over a mount and would hide the app.
if STATIC.is_dir():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
else:

    @app.get("/", include_in_schema=False)
    def index_missing() -> dict:
        return {"error": "the web app is not built", "fix": "cd web && npm install && npm run build"}
