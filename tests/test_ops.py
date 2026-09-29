"""The Pipeline tab's status reads. Airflow is replaced by a small local server that answers the
same four paths, so these run with no containers."""

import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from finplat.ops import LAKE_TABLES, airflow_check, airflow_jobs, health, lake_tables, trigger
from finplat.pipeline import BRONZE, QUARANTINE
from tests.test_quality import load

RUN = {
    "dag_run_id": "manual__1",
    "state": "success",
    "run_type": "manual",
    "start_date": "2026-09-29T00:00:00+00:00",
    "end_date": "2026-09-29T00:01:30+00:00",
}
ANSWERS = {
    "/api/v2/dags": {
        "dags": [{"dag_id": "transactions_to_delta", "is_paused": True, "timetable_summary": "0 0 * * *"}]
    },
    "/api/v2/dags/transactions_to_delta": {"dag_id": "transactions_to_delta", "is_paused": True},
    "/api/v2/dags/transactions_to_delta/dagRuns?order_by=-run_after&limit=1": {"dag_runs": [RUN]},
    "/api/v2/dags/transactions_to_delta/dagRuns/manual__1/taskInstances": {
        "task_instances": [
            {"task_id": "silver", "state": "success", "start_date": "2026-09-29T00:00:40+00:00", "duration": 12.0},
            {"task_id": "bronze", "state": "success", "start_date": "2026-09-29T00:00:01+00:00", "duration": 30.5},
        ]
    },
    "/api/v2/monitor/health": {"scheduler": {"status": "unhealthy"}},
}


class FakeAirflow(BaseHTTPRequestHandler):
    def do_GET(self):
        self._answer(ANSWERS.get(self.path))

    def do_POST(self):
        self._answer({"dag_run_id": "manual__2", "state": "queued"})

    def _answer(self, body):
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body or {}).encode())

    def log_message(self, *_):
        pass


@pytest.fixture
def airflow():
    server = HTTPServer(("127.0.0.1", 0), FakeAirflow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_the_lake_status_reads_counts_from_the_log(tmp_path):
    lake = str(tmp_path / "lake")
    load(lake, date(2026, 9, 1), rows=2_000)
    load(lake, date(2026, 9, 2), rows=2_000)

    tables = {row["table"]: row for row in lake_tables(lake)}
    assert list(tables) == LAKE_TABLES
    assert tables[BRONZE]["rows"] >= 4_000
    assert tables[BRONZE]["latest_batch"]["id"] == "2026-09-02"
    # Quarantine holds the rows the generator broke on purpose, so the latest batch has some.
    assert tables[QUARANTINE]["latest_batch"]["rows"] > 0


def test_a_missing_table_is_a_row_not_an_error(tmp_path):
    tables = lake_tables(str(tmp_path / "empty"))
    assert [row["exists"] for row in tables] == [False] * len(LAKE_TABLES)


def test_airflow_jobs_carry_the_latest_run_and_its_tasks_in_order(airflow):
    answer = airflow_jobs(airflow)

    assert answer["reachable"]
    dag = answer["dags"][0]
    assert dag["paused"]
    assert dag["schedule"] == "Daily 00:00 UTC"
    assert dag["latest"]["duration"] == 90.0
    assert [task["task_id"] for task in dag["tasks"]] == ["bronze", "silver"]


def test_an_unreachable_airflow_is_reported_not_raised():
    answer = airflow_jobs("http://127.0.0.1:1")
    assert answer["reachable"] is False
    assert answer["dags"] == []


def test_a_trigger_says_when_the_run_waits_on_a_paused_dag(airflow):
    assert trigger(airflow, "transactions_to_delta") == {"dag_run_id": "manual__2", "state": "queued", "paused": True}


def test_a_failing_check_is_down_with_its_reason(airflow):
    rows = health({"ok": lambda: "fine", "Airflow": airflow_check(airflow)})
    assert [(row["name"], row["status"]) for row in rows] == [("ok", "healthy"), ("Airflow", "down")]
    assert "unhealthy" in rows[1]["detail"]
