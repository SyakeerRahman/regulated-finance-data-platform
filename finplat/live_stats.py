"""Rolling counts for the live dashboard: volume, alerts, scoring time, writes to the lake, the
score histogram, where alerts come from, and a short log of what the service did.

The browser can reload at any moment, so the history lives in the service and not in the page.
Everything here counts since the service started. A restart starts again from zero.
"""

from collections import Counter, deque
from dataclasses import dataclass, field

BUCKET_SECONDS = 10
HOUR = 3600
# Two hours, so the last hour always has a whole hour before it to compare with.
KEEP_BUCKETS = 2 * HOUR // BUCKET_SECONDS
# 50 bins of 0.02. With 20 bins the threshold bin holds mostly passes and reads as an alert bar.
SCORE_BINS = 50
TOP = 5
# The latency tile reads the last this-many payments, so it answers "how fast is it now".
RECENT_LATENCIES = 1_000
EVENTS = 40


@dataclass
class _Bucket:
    start: int
    scored: int = 0
    alerts: int = 0
    written: int = 0
    # Raw timings while the bucket fills, then three numbers once it closes. 500 floats a bucket
    # for two hours would be 360,000 floats held for a chart that shows three lines.
    latencies: list[float] = field(default_factory=list)
    percentiles: tuple[float, float, float] | None = None

    def close(self) -> None:
        if self.latencies and self.percentiles is None:
            self.percentiles = percentiles(self.latencies)
            self.latencies = []

    def latency(self) -> tuple[float, float, float] | None:
        return self.percentiles or (percentiles(self.latencies) if self.latencies else None)


class LiveStats:
    def __init__(self, now: float) -> None:
        self.started_at = now
        self._buckets: deque[_Bucket] = deque(maxlen=KEEP_BUCKETS)
        self._latencies: deque[float] = deque(maxlen=RECENT_LATENCIES)
        self.histogram = [0] * SCORE_BINS
        self.categories: Counter[str] = Counter()
        self.countries: Counter[str] = Counter()
        self.events: deque[dict] = deque(maxlen=EVENTS)

    def _bucket(self, now: float) -> _Bucket:
        start = _bucket_start(now)
        if not self._buckets or self._buckets[-1].start != start:
            if self._buckets:
                self._buckets[-1].close()
            self._buckets.append(_Bucket(start))
        return self._buckets[-1]

    def add(self, result: dict, now: float) -> None:
        bucket = self._bucket(now)
        bucket.scored += 1
        if "latency_ms" in result:
            bucket.latencies.append(result["latency_ms"])
            self._latencies.append(result["latency_ms"])
        # A score of exactly 1.0 belongs in the top bin, not in a bin past the end.
        self.histogram[min(int(result["score"] * SCORE_BINS), SCORE_BINS - 1)] += 1
        if result["alert"]:
            bucket.alerts += 1
            self.categories[result["merchant_category"]] += 1
            self.countries[result["country"]] += 1

    def record_write(self, rows: int, now: float) -> None:
        self._bucket(now).written += rows

    def note(self, stage: str, text: str, now: float, level: str = "ok") -> None:
        """One line in the event log. `level` is ok, warning or error."""
        self.events.appendleft({"at": now, "stage": stage, "text": text, "level": level})

    @property
    def alerts_total(self) -> int:
        return sum(self.categories.values())

    def recent_latency(self) -> tuple[float, float, float] | None:
        """p50, p95 and p99 of the last payments, or None before the first one."""
        return percentiles(list(self._latencies)) if self._latencies else None

    def snapshot(self, now: float) -> dict:
        end = _bucket_start(now)
        buckets = {bucket.start: bucket for bucket in self._buckets}

        # One slot for every bucket of the last hour, zero where the feed was stopped. Slots from
        # before the service started are left out, so a fresh start does not draw an hour of zeros.
        first = max(end - HOUR + BUCKET_SECONDS, _bucket_start(self.started_at))
        slots = [buckets.get(start) for start in range(first, end + 1, BUCKET_SECONDS)]
        latency = [slot.latency() if slot else None for slot in slots]

        # The previous hour exists only once the service has seen all of it. A partial hour would
        # make every change look like growth.
        previous = None
        if now - self.started_at >= 2 * HOUR:
            previous = _totals(buckets, end - 2 * HOUR, end - HOUR)

        return {
            "bucket_seconds": BUCKET_SECONDS,
            "first": first,
            "scored": [slot.scored if slot else 0 for slot in slots],
            "alerts": [slot.alerts if slot else 0 for slot in slots],
            "written": [slot.written if slot else 0 for slot in slots],
            "latency": {
                name: [round(value[index], 2) if value else None for value in latency]
                for index, name in enumerate(("p50", "p95", "p99"))
            },
            "latency_p95_ms": round(percentiles(list(self._latencies))[1], 2) if self._latencies else None,
            "last_hour": _totals(buckets, end - HOUR, end),
            "previous_hour": previous,
            "histogram": self.histogram,
            "categories": self.categories.most_common(TOP),
            "countries": self.countries.most_common(TOP),
            "alerts_total": self.alerts_total,
            "events": list(self.events),
        }


def percentiles(values: list[float]) -> tuple[float, float, float]:
    """p50, p95 and p99 by nearest rank. Close enough for a chart, and needs no numpy."""
    ordered = sorted(values)
    last = len(ordered) - 1
    return tuple(ordered[min(last, round(share * last))] for share in (0.5, 0.95, 0.99))


def _bucket_start(moment: float) -> int:
    return int(moment // BUCKET_SECONDS) * BUCKET_SECONDS


def _totals(buckets: dict[int, _Bucket], after: int, until: int) -> dict[str, int]:
    """Counts in the buckets that start after `after`, up to and including `until`."""
    inside = [bucket for start, bucket in buckets.items() if after < start <= until]
    return {
        "scored": sum(bucket.scored for bucket in inside),
        "alerts": sum(bucket.alerts for bucket in inside),
        "written": sum(bucket.written for bucket in inside),
    }
