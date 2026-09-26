from datetime import UTC, datetime

from airflow.sdk import dag, get_current_context, task

from finplat.generate import generate
from finplat.pipeline import build_gold, load_bronze, load_labels, refine_silver
from finplat.settings import get_settings


@dag(
    schedule="@daily",
    start_date=datetime(2026, 9, 1, tzinfo=UTC),
    catchup=False,
    # Silver merges and gold rebuilds read the whole table, so two runs at once would race.
    max_active_runs=1,
    tags=["weekend-1"],
)
def transactions_to_delta():
    @task
    def bronze() -> str:
        context = get_current_context()
        # A manual run in Airflow 3 has no logical date, so fall back to when the run was queued.
        day = (context.get("logical_date") or context["dag_run"].run_after).date()
        settings = get_settings()
        batch_id = day.isoformat()
        transactions, labels = generate(day, settings.rows_per_batch, settings.seed, settings.accounts)
        load_bronze(settings.lake_uri, transactions, batch_id)
        # The verdicts land with the batch but are dated weeks later, so training must filter them.
        load_labels(settings.lake_uri, labels, batch_id)
        return batch_id

    @task
    def silver(batch_id: str) -> dict[str, int]:
        return refine_silver(get_settings().lake_uri, batch_id)

    @task
    def gold() -> int:
        return build_gold(get_settings().lake_uri)

    silver(bronze()) >> gold()


transactions_to_delta()
