from datetime import UTC, datetime

import pandas as pd
from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowSkipException

from finplat.settings import get_settings
from finplat.train import NotEnoughLabels, train_and_log


@dag(
    schedule="@weekly",
    start_date=datetime(2026, 9, 1, tzinfo=UTC),
    catchup=False,
    # A registry with two runs writing versions at once is a registry nobody can read.
    max_active_runs=1,
    tags=["model"],
)
def train_fraud_model():
    @task
    def train() -> dict[str, float]:
        context = get_current_context()
        # The cutoff is the run date. A label that arrives after it must stay invisible, the same
        # way it is invisible to a model that runs in production today.
        as_of = context.get("logical_date") or context["dag_run"].run_after
        settings = get_settings()
        try:
            result = train_and_log(settings.lake_uri, pd.Timestamp(as_of), settings.mlflow_tracking_uri)
        except NotEnoughLabels as error:
            # Skipped, not failed: nothing is broken, the verdicts have not arrived yet. Skipped
            # still shows apart from success, so a week with no new model does not pass unseen.
            raise AirflowSkipException(str(error)) from error
        return result.metrics

    train()


train_fraud_model()
