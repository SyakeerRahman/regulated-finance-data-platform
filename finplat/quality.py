"""Data quality checks for one batch.

A pipeline rarely fails loudly. It usually succeeds and produces rubbish, so the checks that
matter are not the row rules. They are the ones that compare today against the days before it:
volume, freshness, and the shape of the numbers.

Every check reads the tables. None of them takes a count from `refine_silver`, because a check
that trusts the process it checks is not a check.

A critical failure stops the DAG, so bad data never reaches gold. A warning is recorded and
passes.
"""

from dataclasses import dataclass, field

import pandas as pd
from deltalake import DeltaTable

from finplat.pipeline import BRONZE, QUARANTINE, SILVER, replace_batch

QUALITY = "quality/checks"
HISTORY_COLUMNS = ["batch_id", "checked_at", "check", "severity", "passed", "value", "threshold", "detail"]

CRITICAL = "critical"
WARNING = "warning"

# A batch this far below or above the recent average is not a quiet day. It is a broken feed.
VOLUME_TOLERANCE = 0.5
FRESHNESS_HOURS = 25
# Two machines never agree to the microsecond. A few minutes ahead of our clock is skew, not a
# producer writing tomorrow's date.
CLOCK_SKEW_HOURS = 0.25
MAX_QUARANTINE_RATE = 0.05
MAX_DUPLICATE_RATE = 0.03
MEDIAN_TOLERANCE = 3.0
HISTORY_DAYS = 7

# pandas 3 reports a text column as "str", where pandas 2 reported "object". A contract written
# against the older name passes nothing.
EXPECTED_BRONZE = {
    "transaction_id": "str",
    "account_id": "str",
    "merchant_category": "str",
    "amount": "float64",
    "currency": "str",
    "country": "str",
    "channel": "str",
    "ts": "datetime64[us, UTC]",
    "batch_id": "str",
    "ingested_at": "datetime64[us, UTC]",
}


@dataclass
class Check:
    name: str
    severity: str
    passed: bool
    value: float
    threshold: float
    detail: str = ""


@dataclass
class Report:
    batch_id: str
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.passed]

    @property
    def blocking(self) -> list[Check]:
        return [check for check in self.failures if check.severity == CRITICAL]

    def raise_on_critical(self) -> None:
        if self.blocking:
            named = "; ".join(f"{c.name}: {c.detail}" for c in self.blocking)
            raise DataQualityError(f"batch {self.batch_id} failed {len(self.blocking)} critical checks. {named}")

    def summary(self) -> dict[str, int]:
        return {
            "checks": len(self.checks),
            "failed": len(self.failures),
            "critical": len(self.blocking),
        }


class DataQualityError(RuntimeError):
    """Raised when a critical check fails. Airflow stops the run, so gold keeps yesterday."""


def run_checks(lake: str, batch_id: str, as_of: pd.Timestamp | None = None) -> Report:
    """Every check for one batch. `as_of` is the clock, so a test can place itself in time."""
    now = as_of or pd.Timestamp.now(tz="UTC")
    bronze = DeltaTable(f"{lake}/{BRONZE}")
    batch = bronze.to_pandas(filters=[("batch_id", "=", batch_id)])

    report = Report(batch_id=batch_id)
    report.checks.append(_schema(batch))
    report.checks.append(_freshness(batch, now))
    report.checks.append(_volume(bronze, batch_id, len(batch)))
    report.checks.append(_quarantine_rate(lake, batch_id, len(batch)))
    report.checks.append(_duplicate_rate(lake, batch_id, len(batch)))
    report.checks.append(_amount_median(lake, batch_id))
    return report


def record(lake: str, report: Report, as_of: pd.Timestamp | None = None) -> int:
    """Store the results, replacing an earlier run of the same batch."""
    frame = pd.DataFrame(
        [
            {
                "batch_id": report.batch_id,
                "checked_at": as_of or pd.Timestamp.now(tz="UTC"),
                "check": check.name,
                "severity": check.severity,
                "passed": check.passed,
                "value": float(check.value),
                "threshold": float(check.threshold),
                "detail": check.detail,
            }
            for check in report.checks
        ]
    )
    replace_batch(f"{lake}/{QUALITY}", frame, report.batch_id)
    return len(frame)


def _schema(batch: pd.DataFrame) -> Check:
    actual = {name: str(dtype) for name, dtype in batch.dtypes.items()}
    wrong = [
        f"{name} is {actual.get(name, 'absent')}, expected {want}"
        for name, want in EXPECTED_BRONZE.items()
        if actual.get(name) != want
    ]
    return Check(
        name="schema",
        severity=CRITICAL,
        passed=not wrong,
        value=len(EXPECTED_BRONZE) - len(wrong),
        threshold=len(EXPECTED_BRONZE),
        detail="; ".join(wrong) if wrong else "every column present with the expected type",
    )


def _freshness(batch: pd.DataFrame, now: pd.Timestamp) -> Check:
    if batch.empty:
        return Check("freshness", CRITICAL, False, 0.0, FRESHNESS_HOURS, "the batch is empty")
    age = (now - batch["ingested_at"].max()).total_seconds() / 3600
    if age < -CLOCK_SKEW_HOURS:
        # A row dated ahead of the clock is its own fault: a wrong timezone, or a producer whose
        # clock drifted. It is never a fresh batch, so it must not read as one.
        detail = f"the newest row is dated {-age:.1f} hours in the future"
    else:
        detail = f"the newest row landed {age:.1f} hours ago"
    return Check(
        name="freshness",
        severity=CRITICAL,
        passed=-CLOCK_SKEW_HOURS <= age <= FRESHNESS_HOURS,
        value=round(age, 2),
        threshold=FRESHNESS_HOURS,
        detail=detail,
    )


def _volume(bronze: DeltaTable, batch_id: str, rows: int) -> Check:
    history = bronze.to_pandas(columns=["batch_id"])["batch_id"].value_counts()
    earlier = history.drop(index=batch_id, errors="ignore").sort_index().tail(HISTORY_DAYS)
    if earlier.empty:
        return Check("volume", CRITICAL, True, rows, 0, "no earlier batch to compare against")

    average = float(earlier.mean())
    low, high = average * (1 - VOLUME_TOLERANCE), average * (1 + VOLUME_TOLERANCE)
    return Check(
        name="volume",
        severity=CRITICAL,
        passed=low <= rows <= high,
        value=rows,
        threshold=round(average, 1),
        detail=f"{rows} rows against a {len(earlier)}-batch average of {average:.0f}",
    )


def _rate(lake: str, table: str, batch_id: str, received: int) -> float:
    path = f"{lake}/{table}"
    if not received or not DeltaTable.is_deltatable(path):
        return 0.0
    return len(DeltaTable(path).to_pandas(filters=[("batch_id", "=", batch_id)])) / received


def _quarantine_rate(lake: str, batch_id: str, received: int) -> Check:
    rate = _rate(lake, QUARANTINE, batch_id, received)
    return Check(
        name="quarantine rate",
        severity=WARNING,
        passed=rate <= MAX_QUARANTINE_RATE,
        value=round(rate, 4),
        threshold=MAX_QUARANTINE_RATE,
        detail=f"{rate:.2%} of the batch was refused",
    )


def _duplicate_rate(lake: str, batch_id: str, received: int) -> Check:
    quarantined = _rate(lake, QUARANTINE, batch_id, received) * received
    accepted = _rate(lake, SILVER, batch_id, received) * received
    rate = (received - quarantined - accepted) / received if received else 0.0
    return Check(
        name="duplicate rate",
        severity=WARNING,
        passed=rate <= MAX_DUPLICATE_RATE,
        value=round(rate, 4),
        threshold=MAX_DUPLICATE_RATE,
        detail=f"{rate:.2%} of the batch was a repeat of a row in the same batch",
    )


def _amount_median(lake: str, batch_id: str) -> Check:
    silver = DeltaTable(f"{lake}/{SILVER}").to_pandas(columns=["batch_id", "amount"])
    today = silver[silver["batch_id"] == batch_id]["amount"]
    earlier_ids = sorted(set(silver["batch_id"]) - {batch_id})[-HISTORY_DAYS:]
    earlier = silver[silver["batch_id"].isin(earlier_ids)]["amount"]

    if earlier.empty or today.empty:
        return Check("amount median", WARNING, True, 0.0, MEDIAN_TOLERANCE, "no earlier batch to compare against")

    ratio = float(today.median() / earlier.median())
    return Check(
        name="amount median",
        severity=WARNING,
        passed=1 / MEDIAN_TOLERANCE <= ratio <= MEDIAN_TOLERANCE,
        value=round(ratio, 3),
        threshold=MEDIAN_TOLERANCE,
        detail=f"the median is {ratio:.2f}x the median of the {len(earlier_ids)} batches before it",
    )


def history(lake: str, days: int = 30) -> pd.DataFrame:
    """The last few batches of results, for the grid on the dashboard."""
    path = f"{lake}/{QUALITY}"
    # A lake that has never run a batch has no table yet. That is an empty grid, not an error.
    if not DeltaTable.is_deltatable(path):
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    frame = DeltaTable(path).to_pandas()
    recent = sorted(frame["batch_id"].unique())[-days:]
    return frame[frame["batch_id"].isin(recent)].sort_values(["batch_id", "check"], ignore_index=True)
