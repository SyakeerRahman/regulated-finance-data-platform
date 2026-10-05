"""The /metrics endpoint and the text it writes. Prometheus refuses a malformed line, and then
drops the whole scrape, so the format is tested as text."""

import re
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api.main
from finplat import metrics
from finplat.live_stats import LiveStats
from tests.test_ai import checked_lake  # noqa: F401 - pytest finds fixtures by name
from tests.test_alerts import store, test_dsn  # noqa: F401
from tests.test_api import Engine

# One sample line: a name, optional labels, and a number.
SAMPLE = re.compile(r'^[a-z_]+(\{([a-z_]+="([^"\\]|\\.)*",?)*\})? -?[0-9.e+-]+$')
DRIFT = {
    "live_rows": 500,
    "features": [{"feature": "amount", "psi": 0.31, "level": "significant"}],
    "prediction": {"psi": 0.02, "level": "stable"},
}


def parse(text: str) -> dict[str, list[str]]:
    """Each metric name with its sample lines. Fails on any line Prometheus would refuse."""
    found: dict[str, list[str]] = {}
    for line in text.strip().splitlines():
        if line.startswith("#"):
            assert re.match(r"^# (HELP|TYPE) [a-z_]+ .+$", line), line
            continue
        assert SAMPLE.match(line), line
        found.setdefault(re.split(r"[{ ]", line)[0], []).append(line)
    return found


def test_a_label_with_quotes_or_a_newline_is_escaped():
    text = metrics.render([metrics.Metric("x", "gauge", "h").add(1, check='say "hi"\nnow')])
    assert 'x{check="say \\"hi\\"\\nnow"} 1.0' in text
    parse(text)


def test_a_large_counter_keeps_every_digit():
    assert "x 1234567.0" in metrics.render([metrics.Metric("x", "counter", "h").add(1_234_567)])


def test_the_newest_batch_age_and_its_failed_gate(checked_lake):  # noqa: F811
    from finplat.quality import history

    frame = history(checked_lake)
    newest = pd.Timestamp(frame["checked_at"].max())
    found = {m.name: m for m in metrics.quality(frame, newest + pd.Timedelta(hours=3))}

    assert found["finplat_last_batch_age_hours"].samples[0][1] == pytest.approx(3.0)
    assert found["finplat_quality_gate_failed"].samples[0][1] == 1.0
    freshness = [v for labels, v in found["finplat_quality_check_passed"].samples if labels["check"] == "freshness"]
    assert freshness == [0.0]


class LiveEngine(Engine):
    def __init__(self, store, lake: str) -> None:  # noqa: F811
        super().__init__(store)
        self.lake = lake
        self.scored = 1_500
        self.stats = LiveStats(time.time())
        self.stats.add(
            {"score": 0.97, "alert": True, "latency_ms": 4.0, "merchant_category": "fuel", "country": "US"},
            time.time(),
        )


@pytest.fixture
def scrape(store, checked_lake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(api.main, "engine", LiveEngine(store, checked_lake))
    monkeypatch.setattr(api.main, "_cache", {})
    calls = []
    monkeypatch.setattr(api.main, "model_drift", lambda: calls.append(1) or DRIFT)
    client = TestClient(api.main.app)
    return client, calls


def test_metrics_publishes_the_model_the_data_and_the_service(scrape):
    client, _ = scrape
    answer = client.get("/metrics")

    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("text/plain; version=0.0.4")
    found = parse(answer.text)
    for name in (
        "finplat_scored_total",
        "finplat_alerts_total",
        "finplat_score_latency_ms",
        "finplat_drift_psi",
        "finplat_quality_check_passed",
        "finplat_quality_gate_failed",
        "finplat_last_batch_age_hours",
        "finplat_open_alerts",
        "finplat_llm_calls_used",
        "finplat_llm_calls_limit",
        "finplat_source_up",
    ):
        assert name in found, name
    assert 'finplat_scored_total{model_version="3"} 1500.0' in found["finplat_scored_total"]
    assert 'finplat_drift_psi{feature="amount",by_design="false",model_version="3"} 0.31' in found["finplat_drift_psi"]
    assert all(line.endswith(" 1.0") for line in found["finplat_source_up"])


def test_a_second_scrape_takes_the_drift_from_the_cache(scrape):
    client, calls = scrape
    client.get("/metrics")
    client.get("/metrics")
    assert calls == [1]


def test_a_failed_source_shows_as_down_and_the_rest_still_report(scrape, monkeypatch):
    client, _ = scrape

    def broken():
        raise RuntimeError("MLflow is down")

    monkeypatch.setattr(api.main, "model_drift", broken)
    found = parse(client.get("/metrics").text)

    assert 'finplat_source_up{source="drift"} 0.0' in found["finplat_source_up"]
    assert "finplat_drift_psi" not in found
    assert "finplat_quality_gate_failed" in found
