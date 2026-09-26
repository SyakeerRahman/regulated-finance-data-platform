"""The live path: invent a transaction, score it, push it to the browser.

    uv run uvicorn api.main:app --port 8097 --reload

Stage E replaces the in-memory state with Redis and the alert list with Postgres, and puts a
React app in front. The shape stays: one loop in, one score, one stream out.
"""

import asyncio
import contextlib
import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from deltalake import DeltaTable
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from finplat.features import FEATURE_COLUMNS, AccountHistory, row_features
from finplat.generate import generate
from finplat.pipeline import SILVER
from finplat.registry import live_version, load_production, production_threshold
from finplat.settings import get_settings

RECENT = 200
DEFAULT_RATE = 1.0

STATIC = Path(__file__).parent / "static"


class Engine:
    """One generator loop, one model, and the last few hundred results."""

    def __init__(self) -> None:
        settings = get_settings()
        self.model = load_production(settings.mlflow_tracking_uri)
        self.model_version = live_version(settings.mlflow_tracking_uri)
        self.threshold = production_threshold(settings.mlflow_tracking_uri)
        self.history = AccountHistory()
        self.warmed = self._warm(settings.lake_uri)
        self.recent: deque[dict] = deque(maxlen=RECENT)
        self.alerts: deque[dict] = deque(maxlen=RECENT)
        self.listeners: set[asyncio.Queue] = set()
        self.rate = DEFAULT_RATE
        self.scored = 0
        self._task: asyncio.Task | None = None
        # One day of transactions, replayed in a loop. The generator is the same one the batch
        # pipeline uses, so the live data and the training data come from one place.
        self._pool = self._fill_pool(settings)
        self._next = 0

    def _warm(self, lake: str) -> int:
        """Load the account history the batch pipeline already built. Redis holds this in stage E."""
        silver = DeltaTable(f"{lake}/{SILVER}").to_pandas(columns=["account_id", "amount"])
        return self.history.warm(silver)

    @staticmethod
    def _fill_pool(settings) -> list[dict]:
        transactions, _ = generate(datetime.now(UTC).date(), 20_000, settings.seed, settings.accounts)
        clean = transactions[transactions["account_id"].notna() & (transactions["amount"] > 0)]
        # Shuffled, because the feed stamps its own clock on each row. In time order the pool
        # opens on the small hours, where fraud concentrates, and the first minute is all alerts.
        return clean.sample(frac=1, random_state=settings.seed).to_dict("records")

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            self.publish(self.score(self._take()))
            await asyncio.sleep(1 / self.rate)

    def _take(self) -> dict:
        transaction = dict(self._pool[self._next % len(self._pool)])
        self._next += 1
        # The pool is one fixed day. Stamp the real clock on it so the feed reads as live.
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
        self.scored += 1

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
            "alert": score >= self.threshold,
            "model_version": self.model_version,
        }

    def publish(self, result: dict) -> None:
        self.recent.appendleft(result)
        if result["alert"]:
            self.alerts.appendleft(result)
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
        "model_version": engine.model_version,
        "threshold": round(engine.threshold, 4),
        "recent": list(engine.recent)[:50],
    }


@app.post("/api/start")
async def start(rate: float = DEFAULT_RATE) -> dict:
    # async, not sync: FastAPI runs a sync endpoint in a worker thread, and asyncio.create_task
    # needs the loop that is running in the main thread.
    engine.rate = max(0.1, min(rate, 50.0))
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


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
