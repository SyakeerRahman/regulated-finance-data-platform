from datetime import UTC, datetime, timedelta

from airflow.configuration import conf
from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

from finplat.generate import generate
from finplat.pipeline import build_gold, load_bronze, load_labels, refine_silver
from finplat.quality import record, run_checks
from finplat.retention import delete_old_files, drop_expired, vacuum_all
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
    def quality(batch_id: str) -> dict[str, int]:
        """Stop here when the batch is wrong. Gold then keeps yesterday, which is correct data,
        rather than being rebuilt from a feed that broke overnight."""
        lake = get_settings().lake_uri
        report = run_checks(lake, batch_id)
        record(lake, report)
        report.raise_on_critical()
        return report.summary()

    @task
    def gold() -> int:
        return build_gold(get_settings().lake_uri)

    # all_done: the disk fills whether or not today's batch was good. A feed that stays broken
    # for a week must not also stop retention for a week.
    # Retries: the live feed appends to bronze while this deletes from it, and a Delta commit
    # that loses that race fails rather than overwrite. The next attempt sees the new version.
    @task(trigger_rule="all_done", retries=2, retry_delay=timedelta(minutes=1))
    def retention() -> dict[str, dict[str, int]]:
        context = get_current_context()
        today = (context.get("logical_date") or context["dag_run"].run_after).date()
        lake = get_settings().lake_uri
        return {"deleted_rows": drop_expired(lake, today), "vacuumed_files": vacuum_all(lake)}

    @task(trigger_rule="all_done")
    def logs() -> int:
        return delete_old_files(conf.get("logging", "base_log_folder"))

    # A run takes the state of its last tasks. retention and logs run on all_done, so they pass
    # after a failed quality gate, and on 2026-10-03 a run with gold never built showed success.
    # This task runs only when a task before it failed, and it fails the run with that task.
    @task(trigger_rule="one_failed")
    def verdict() -> None:
        raise AirflowFailException("a task in this run failed. The red task above says which")

    batch_id = bronze()
    cleaned, checked, built, kept, cleared = silver(batch_id), quality(batch_id), gold(), retention(), logs()
    cleaned >> checked >> built >> kept >> cleared
    [batch_id, cleaned, checked, built, kept, cleared] >> verdict()


transactions_to_delta()
