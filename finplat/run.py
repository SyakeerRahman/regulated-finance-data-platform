"""Run the pipeline for one day without Airflow.

uv run python -m finplat.run 2026-09-01
"""

import sys
from datetime import date

from finplat.generate import generate
from finplat.pipeline import build_gold, load_bronze, load_labels, refine_silver
from finplat.quality import record, run_checks
from finplat.settings import get_settings


def main(day: date) -> None:
    settings = get_settings()
    batch_id = day.isoformat()
    transactions, labels = generate(day, settings.rows_per_batch, settings.seed, settings.accounts)
    print(f"bronze  {load_bronze(settings.lake_uri, transactions, batch_id)} rows landed for {batch_id}")
    print(f"labels  {load_labels(settings.lake_uri, labels, batch_id)} verdicts stored")
    print(f"silver  {refine_silver(settings.lake_uri, batch_id)}")

    report = run_checks(settings.lake_uri, batch_id)
    record(settings.lake_uri, report)
    for check in report.checks:
        print(f"        {'pass' if check.passed else check.severity.upper():<8} {check.name:<16} {check.detail}")
    report.raise_on_critical()

    print(f"gold    {build_gold(settings.lake_uri)} feature rows")


if __name__ == "__main__":
    main(date.fromisoformat(sys.argv[1]))
