"""What the Pipeline tab shows: the lake's tables, the Airflow jobs, and whether each service
answers.

Nothing here raises. A service that is down becomes a row that says so, because a status page
that fails when a service fails is no use on the day it is needed. Everything is read-only except
`trigger`, which starts one DAG run.
"""

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime

from finplat.pipeline import BRONZE, GOLD, LABELS, QUARANTINE, SILVER
from finplat.quality import QUALITY

LAKE_TABLES = [BRONZE, QUARANTINE, SILVER, LABELS, GOLD, QUALITY]
TIMEOUT_SECONDS = 3

# The two schedules this project uses, in words. Anything else is shown as the cron line.
SCHEDULES = {"0 0 * * *": "Daily 00:00 UTC", "0 0 * * 0": "Sundays 00:00 UTC"}


def lake_tables(lake: str) -> list[dict]:
    """Rows, files, size and last write of each table, read from the Delta log alone.

    The log keeps a row count and a size for every file, so this reads no table data.
    """
    import pyarrow as pa
    from deltalake import DeltaTable

    out = []
    for name in LAKE_TABLES:
        try:
            table = DeltaTable(f"{lake}/{name}")
            files = pa.table(table.get_add_actions(flatten=True)).to_pandas()
        except Exception as error:  # noqa: BLE001 - a missing table is a row, not a crash
            out.append({"table": name, "exists": False, "error": str(error)})
            continue

        row = {
            "table": name,
            "exists": True,
            "version": table.version(),
            "files": len(files),
            "rows": int(files["num_records"].sum()) if len(files) else 0,
            "size_bytes": int(files["size_bytes"].sum()) if len(files) else 0,
            "last_write": _iso_ms(int(files["modification_time"].max())) if len(files) else None,
        }
        if "partition.batch_id" in files:
            by_batch = files.groupby("partition.batch_id")["num_records"].sum()
            # The daily batches, named by date. Live and analyst partitions are not batches.
            daily = sorted(batch for batch in by_batch.index if batch[:1].isdigit())
            if daily:
                row["latest_batch"] = {"id": daily[-1], "rows": int(by_batch[daily[-1]])}
        out.append(row)
    return out


def airflow_jobs(base_url: str) -> dict:
    """Each DAG with its schedule, its latest run, and the tasks of that run."""
    try:
        dags = _get(f"{base_url}/api/v2/dags")["dags"]
    except Exception as error:  # noqa: BLE001
        return {"reachable": False, "error": _short(error), "dags": []}

    out = []
    for dag in sorted(dags, key=lambda item: item["dag_id"]):
        dag_id = dag["dag_id"]
        schedule = dag.get("timetable_summary") or ""
        runs = _get(f"{base_url}/api/v2/dags/{dag_id}/dagRuns?order_by=-run_after&limit=1").get("dag_runs", [])
        latest = runs[0] if runs else None
        tasks = []
        if latest:
            instances = _get(f"{base_url}/api/v2/dags/{dag_id}/dagRuns/{latest['dag_run_id']}/taskInstances")
            tasks = sorted(
                (
                    {
                        "task_id": task["task_id"],
                        "state": task.get("state"),
                        "start": task.get("start_date"),
                        "end": task.get("end_date"),
                        "duration": task.get("duration"),
                    }
                    for task in instances.get("task_instances", [])
                ),
                key=lambda task: task["start"] or "~",
            )
        out.append(
            {
                "dag_id": dag_id,
                "paused": bool(dag.get("is_paused")),
                "schedule": SCHEDULES.get(schedule, schedule),
                "next_run": dag.get("next_dagrun_run_after") or dag.get("next_dagrun_logical_date"),
                "latest": _run(latest),
                "tasks": tasks,
            }
        )
    return {"reachable": True, "dags": out}


def trigger(base_url: str, dag_id: str) -> dict:
    """Start one run now. A paused DAG queues the run and starts it once someone unpauses it."""
    run = _post(f"{base_url}/api/v2/dags/{dag_id}/dagRuns", {"logical_date": None})
    dag = _get(f"{base_url}/api/v2/dags/{dag_id}")
    return {"dag_run_id": run.get("dag_run_id"), "state": run.get("state"), "paused": bool(dag.get("is_paused"))}


def health(checks: dict[str, Callable[[], str]]) -> list[dict]:
    """Run each check with a clock on it. A check returns a short detail, or raises when down."""
    out = []
    for name, check in checks.items():
        started = time.perf_counter()
        try:
            detail, status = check(), "healthy"
        except Exception as error:  # noqa: BLE001
            detail, status = _short(error), "down"
        out.append(
            {"name": name, "status": status, "detail": detail, "ms": round((time.perf_counter() - started) * 1000, 1)}
        )
    return out


def mlflow_check(tracking_uri: str) -> Callable[[], str]:
    return lambda: _text(f"{tracking_uri}/health").strip() or "OK"


def airflow_check(base_url: str) -> Callable[[], str]:
    def check() -> str:
        answer = _get(f"{base_url}/api/v2/monitor/health")
        scheduler = answer.get("scheduler", {}).get("status")
        if scheduler != "healthy":
            raise RuntimeError(f"scheduler is {scheduler}")
        return "Scheduler healthy"

    return check


def _run(run: dict | None) -> dict | None:
    if run is None:
        return None
    start, end = run.get("start_date"), run.get("end_date")
    duration = None
    if start and end:
        duration = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    return {
        "run_id": run["dag_run_id"],
        "state": run.get("state"),
        "run_type": run.get("run_type"),
        "start": start,
        "end": end,
        "duration": duration,
    }


def _get(url: str) -> dict:
    return json.loads(_text(url))


def _text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode()


def _post(url: str, body: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Airflow answered {error.code}: {error.read().decode()[:200]}") from error


def _iso_ms(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).isoformat()


def _short(error: Exception) -> str:
    reason = getattr(error, "reason", None)
    return str(reason or error)[:160]
