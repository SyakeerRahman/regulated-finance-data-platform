"""The service's numbers in the Prometheus text format, for Prometheus to scrape at /metrics.

Written by hand: gauges and counters are a few lines of text each, and the latency percentiles
already come from `LiveStats`, so no histogram type is needed. `AGENTS.md` asks for a library
only past 30 lines.

Each function here takes plain data and returns metrics. The API gathers the data. A source that
fails, for example MLflow, reports `finplat_source_up 0` and leaves out its metrics: one broken
source must not hide the others.
"""

from dataclasses import dataclass, field

import pandas as pd

from finplat.quality import CRITICAL


@dataclass
class Metric:
    name: str
    kind: str  # gauge or counter
    help: str
    samples: list[tuple[dict[str, str], float]] = field(default_factory=list)

    def add(self, value: float, **labels: str) -> "Metric":
        self.samples.append((labels, float(value)))
        return self


def _label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render(metrics: list[Metric]) -> str:
    lines = []
    for metric in metrics:
        lines += [f"# HELP {metric.name} {metric.help}", f"# TYPE {metric.name} {metric.kind}"]
        for labels, value in metric.samples:
            text = ",".join(f'{key}="{_label(item)}"' for key, item in labels.items())
            # repr, not :g. Six digits would turn a counter of 1,234,567 into 1.23457e+06.
            lines.append(f"{metric.name}{{{text}}} {value!r}" if text else f"{metric.name} {value!r}")
    return "\n".join(lines) + "\n"


def live(scored: int, alerted: int, latency: tuple[float, float, float] | None, version: str) -> list[Metric]:
    """What the live scorer did since the service started. A restart starts again from zero."""
    metrics = [
        Metric("finplat_scored_total", "counter", "Payments scored since the service started.").add(
            scored, model_version=version
        ),
        Metric("finplat_alerts_total", "counter", "Alerts raised since the service started.").add(
            alerted, model_version=version
        ),
    ]
    if latency:
        speed = Metric("finplat_score_latency_ms", "gauge", "Scoring time of the last 1,000 payments.")
        for quantile, value in zip(("0.5", "0.95", "0.99"), latency, strict=True):
            speed.add(value, quantile=quantile)
        metrics.append(speed)
    return metrics


def drift(report: dict, version: str) -> list[Metric]:
    """PSI of each feature and of the score, live against the training data of `version`."""
    psi = Metric("finplat_drift_psi", "gauge", "Population stability index, live against training.")
    for row in report["features"]:
        psi.add(row["psi"], feature=row["feature"], model_version=version)
    if report["prediction"]:
        psi.add(report["prediction"]["psi"], feature="score", model_version=version)
    rows = Metric("finplat_drift_live_rows", "gauge", "Live rows the drift check compared.").add(report["live_rows"])
    return [psi, rows]


def quality(frame: pd.DataFrame, now: pd.Timestamp) -> list[Metric]:
    """The checks of the newest batch, and how long ago any batch was checked."""
    if frame.empty:
        return []
    newest = frame[frame["batch_id"] == frame["batch_id"].max()]
    passed = Metric("finplat_quality_check_passed", "gauge", "1 when the check passed on the newest batch.")
    for row in newest.itertuples():
        passed.add(bool(row.passed), check=row.check, severity=row.severity, batch_id=row.batch_id)
    blocked = (~newest["passed"].astype(bool) & (newest["severity"] == CRITICAL)).any()
    age = (now - pd.Timestamp(frame["checked_at"].max())).total_seconds() / 3600
    return [
        passed,
        Metric("finplat_quality_gate_failed", "gauge", "1 when a critical check stopped the newest batch.").add(
            bool(blocked)
        ),
        Metric("finplat_last_batch_age_hours", "gauge", "Hours since the newest quality result.").add(round(age, 3)),
    ]


def service(open_alerts: int, budget: dict) -> list[Metric]:
    return [
        Metric("finplat_open_alerts", "gauge", "Alerts that wait for an analyst.").add(open_alerts),
        Metric("finplat_llm_calls_used", "gauge", "AI calls used today, UTC.").add(budget["used"]),
        Metric("finplat_llm_calls_limit", "gauge", "AI calls allowed each UTC day.").add(budget["per_day"]),
    ]


def sources(up: dict[str, bool]) -> list[Metric]:
    metric = Metric("finplat_source_up", "gauge", "1 when the source answered on this scrape.")
    for name, ok in up.items():
        metric.add(ok, source=name)
    return [metric]
