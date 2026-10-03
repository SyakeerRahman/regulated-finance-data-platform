"""How often the AI analyst is right, measured against the true answer.

    uv run python -m finplat.ai_eval --alerts 100

The agreement rate on the Ask AI tab needs analysts to decide alerts first. This needs nobody.
The data is synthetic, so every payment's true answer can be rebuilt from its id: the day, the
round and the row number go back into the same generator that made it.

The model never sees the answer. The truth is joined after it has spoken.

The number that matters is not the AI's accuracy alone. Every alert already scored above the
threshold, so "always say fraud" is right as often as the fraud model's precision. The AI earns
its place only if it beats that, mostly by spotting the false positives.
"""

import argparse
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from finplat import assistant, prompts
from finplat.alerts import LIKELY_FALSE_POSITIVE, LIKELY_FRAUD, UNSURE, Store
from finplat.feed import POOL_ROWS
from finplat.generate import generate
from finplat.llm import LLM, Budget, LLMError

EXPERIMENT = "ai-analyst"
ID = re.compile(r"^(\d{8})(?:-r(\d+))?-(\d{7})$")
WORKERS = 4


def truth_for(alerts: list[dict], seed: int, accounts: int, rows: int = POOL_ROWS) -> dict[str, bool]:
    """The true answer for each alert whose payment can be rebuilt exactly.

    An alert is left out when its id has another shape, or when the rebuilt payment has another
    amount. That happens to a payment made by an older generator, and a guess there would be a
    wrong answer key.
    """
    groups: dict[tuple[date, int], list[dict]] = {}
    for alert in alerts:
        match = ID.match(alert["transaction_id"])
        if match:
            day = date(int(match[1][:4]), int(match[1][4:6]), int(match[1][6:]))
            groups.setdefault((day, int(match[2] or 0)), []).append(alert)

    truth = {}
    for (day, round_), members in groups.items():
        transactions, labels = generate(day, rows, seed, accounts, round_=round_)
        amounts = transactions.drop_duplicates("transaction_id").set_index("transaction_id")["amount"]
        verdicts = labels.set_index("transaction_id")["is_fraud"]
        for alert in members:
            key = alert["transaction_id"]
            if key in verdicts.index and abs(abs(float(amounts[key])) - float(alert["amount"])) < 0.01:
                truth[key] = bool(verdicts[key])
    return truth


def score(pairs: list[tuple[str, bool]]) -> dict:
    """Suggestions against the truth. `pairs` holds (suggestion, is_fraud) for each alert."""
    total = len(pairs)
    fraud = sum(is_fraud for _, is_fraud in pairs)
    firm = [(s, f) for s, f in pairs if s != UNSURE]
    right = sum((s == LIKELY_FRAUD) == f for s, f in firm)
    said_fraud = [f for s, f in pairs if s == LIKELY_FRAUD]
    said_false = [f for s, f in pairs if s == LIKELY_FALSE_POSITIVE]
    honest = total - fraud

    def share(part: int, whole: int) -> float | None:
        return part / whole if whole else None

    return {
        "alerts": total,
        # What "always say fraud" scores. The AI has to beat this to add anything.
        "baseline_accuracy": share(fraud, total),
        "accuracy": share(right, len(firm)),
        "coverage": share(len(firm), total),
        # Of the payments it called fraud, how many were. Of those it called false, how many were honest.
        "fraud_precision": share(sum(said_fraud), len(said_fraud)),
        "false_positive_precision": share(sum(not f for f in said_false), len(said_false)),
        # Of the real fraud, how much it called fraud. Of the honest payments, how many it caught.
        "fraud_recall": share(sum(said_fraud), fraud),
        "false_positives_caught": share(sum(not f for f in said_false), honest),
        "matrix": {
            suggestion: {
                "fraud": sum(f for s, f in pairs if s == suggestion),
                "honest": sum(not f for s, f in pairs if s == suggestion),
            }
            for suggestion in (LIKELY_FRAUD, LIKELY_FALSE_POSITIVE, UNSURE)
        },
    }


def _sample(store: Store, count: int, seed: int) -> list[dict]:
    with store.connect() as connection:
        rows = connection.execute("select * from alerts order by alert_id").fetchall()
    return random.Random(seed).sample(rows, min(count, len(rows)))


def run(store: Store, llm: LLM, settings, count: int, seed: int = 7) -> dict:
    """Narrate a sample of alerts where needed, join the truth, and score."""
    version = prompts.load("narrate").version
    sample = _sample(store, count * 2, seed)
    truth = truth_for(sample, settings.seed, settings.accounts)
    chosen = [alert for alert in sample if alert["transaction_id"] in truth][:count]

    def suggestion(alert: dict) -> str | None:
        # An answer from the current prompt is reused. An older prompt's answer would score a
        # prompt that is no longer live.
        if alert["ai_suggestion"] and alert["ai_prompt_version"] == version:
            return alert["ai_suggestion"]
        try:
            narrative = assistant.narrate(llm, alert, store.for_account(alert["account_id"]))
        except LLMError:
            return None
        store.save_narrative(alert["alert_id"], narrative, llm.model, version)
        return narrative["suggestion"]

    with ThreadPoolExecutor(WORKERS) as pool:
        answers = list(pool.map(suggestion, chosen))

    pairs = [(answer, truth[alert["transaction_id"]]) for alert, answer in zip(chosen, answers, strict=True) if answer]
    return {
        **score(pairs),
        "failed_calls": answers.count(None),
        "model": llm.model,
        "prompt_version": version,
    }


def log(result: dict, tracking_uri: str) -> str:
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run() as active:
        mlflow.log_params({"model": result["model"], "prompt_version": result["prompt_version"]})
        mlflow.log_metrics(
            {key: value for key, value in result.items() if isinstance(value, int | float) and value is not None}
        )
        mlflow.log_dict(result, "evaluation.json")
        return active.info.run_id


def latest(tracking_uri: str) -> dict | None:
    """The newest evaluation, for the Ask AI tab. None before the first one."""
    from mlflow import MlflowClient

    client = MlflowClient(tracking_uri)
    experiment = client.get_experiment_by_name(EXPERIMENT)
    if experiment is None:
        return None
    runs = client.search_runs([experiment.experiment_id], order_by=["attributes.start_time DESC"], max_results=1)
    if not runs:
        return None
    path = client.download_artifacts(runs[0].info.run_id, "evaluation.json")
    with open(path, encoding="utf-8") as handle:
        return {**json.load(handle), "run_id": runs[0].info.run_id, "at": runs[0].info.start_time}


def main() -> None:
    from finplat.settings import get_settings

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--alerts", type=int, default=100)
    # Change it to grade a prompt on alerts it was not tuned on.
    parser.add_argument("--seed", type=int, default=7)
    arguments = parser.parse_args()

    settings = get_settings()
    store = Store(settings.postgres_dsn)
    llm = LLM(settings.llm_base_url, settings.llm_model, settings.llm_api_key, Budget(settings.llm_daily_calls))
    result = run(store, llm, settings, arguments.alerts, arguments.seed)
    run_id = log(result, settings.mlflow_tracking_uri)
    print(json.dumps({key: value for key, value in result.items() if key != "matrix"}, indent=2))
    print(json.dumps(result["matrix"], indent=2))
    print(f"logged to MLflow, experiment {EXPERIMENT}, run {run_id}")


if __name__ == "__main__":
    main()
