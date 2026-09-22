"""Run the pipeline for one day without Airflow.

uv run python -m finplat.run 2026-09-01
"""

import sys
from datetime import date

from finplat.generate import generate
from finplat.pipeline import build_gold, load_bronze, refine_silver
from finplat.settings import get_settings


def main(day: date) -> None:
    settings = get_settings()
    batch_id = day.isoformat()
    landed = load_bronze(settings.lake_uri, generate(day, settings.rows_per_batch, settings.seed), batch_id)
    print(f"bronze  {landed} rows landed for {batch_id}")
    print(f"silver  {refine_silver(settings.lake_uri, batch_id)}")
    print(f"gold    {build_gold(settings.lake_uri)} feature rows")


if __name__ == "__main__":
    main(date.fromisoformat(sys.argv[1]))
