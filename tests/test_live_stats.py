from finplat.live_stats import BUCKET_SECONDS, EVENTS, HOUR, SCORE_BINS, LiveStats, percentiles

T0 = 1_800_000_000.0


def result(score: float, alert: bool = False, category: str = "grocery", country: str = "MY") -> dict:
    return {"score": score, "alert": alert, "merchant_category": category, "country": country}


def test_a_score_of_one_lands_in_the_top_bin():
    stats = LiveStats(T0)
    stats.add(result(0.0), T0)
    stats.add(result(1.0, alert=True), T0)

    assert stats.histogram[0] == 1
    assert stats.histogram[SCORE_BINS - 1] == 1


def test_only_alerts_count_towards_category_and_country():
    stats = LiveStats(T0)
    stats.add(result(0.1, category="grocery", country="MY"), T0)
    stats.add(result(0.99, alert=True, category="electronics", country="SG"), T0)
    stats.add(result(0.95, alert=True, category="electronics", country="MY"), T0)

    snapshot = stats.snapshot(T0)
    assert snapshot["categories"] == [("electronics", 2)]
    assert dict(snapshot["countries"]) == {"SG": 1, "MY": 1}
    assert snapshot["alerts_total"] == 2


def test_a_stopped_feed_leaves_zeros_not_gaps():
    """The chart reads one slot per bucket. A missing slot would join two points across the gap
    and draw traffic that never happened."""
    stats = LiveStats(T0)
    stats.add(result(0.1), T0)
    stats.add(result(0.1), T0 + 3 * BUCKET_SECONDS)

    snapshot = stats.snapshot(T0 + 3 * BUCKET_SECONDS)
    assert snapshot["scored"] == [1, 0, 0, 1]
    assert snapshot["last_hour"] == {"scored": 2, "alerts": 0, "written": 0}


def test_there_is_no_previous_hour_until_the_service_has_seen_one():
    stats = LiveStats(T0)
    for minute in range(120):
        stats.add(result(0.1), T0 + minute * 60)

    assert stats.snapshot(T0 + HOUR)["previous_hour"] is None

    later = stats.snapshot(T0 + 2 * HOUR)
    assert later["previous_hour"]["scored"] > 0
    assert later["last_hour"]["scored"] > 0
    # The series covers one hour, not the two the service keeps.
    assert len(later["scored"]) == HOUR // BUCKET_SECONDS


def test_latency_percentiles_are_kept_for_each_bucket():
    stats = LiveStats(T0)
    for millis in range(1, 101):
        stats.add({**result(0.1), "latency_ms": float(millis)}, T0)
    # The next bucket closes the first one, which keeps three numbers and drops the raw timings.
    stats.add({**result(0.1), "latency_ms": 500.0}, T0 + BUCKET_SECONDS)

    snapshot = stats.snapshot(T0 + BUCKET_SECONDS)
    assert snapshot["latency"]["p50"][0] == 51.0
    assert snapshot["latency"]["p95"][0] == 95.0
    assert snapshot["latency"]["p99"][1] == 500.0
    assert snapshot["latency_p95_ms"] == 96.0


def test_an_empty_bucket_has_no_latency_rather_than_zero():
    """Zero would draw as the fastest scoring ever seen, at a moment when nothing was scored."""
    stats = LiveStats(T0)
    stats.add({**result(0.1), "latency_ms": 5.0}, T0)
    stats.add({**result(0.1), "latency_ms": 5.0}, T0 + 2 * BUCKET_SECONDS)

    assert stats.snapshot(T0 + 2 * BUCKET_SECONDS)["latency"]["p95"] == [5.0, None, 5.0]


def test_writes_to_the_lake_are_counted_where_they_happened():
    stats = LiveStats(T0)
    stats.add(result(0.1), T0)
    stats.record_write(2_000, T0 + BUCKET_SECONDS)

    snapshot = stats.snapshot(T0 + BUCKET_SECONDS)
    assert snapshot["written"] == [0, 2_000]
    assert snapshot["last_hour"]["written"] == 2_000


def test_the_event_log_is_newest_first_and_bounded():
    stats = LiveStats(T0)
    for index in range(EVENTS + 5):
        stats.note("feed", f"event {index}", T0 + index)

    events = stats.snapshot(T0 + EVENTS + 5)["events"]
    assert len(events) == EVENTS
    assert events[0]["text"] == f"event {EVENTS + 4}"


def test_percentiles_of_one_value_are_that_value():
    assert percentiles([7.0]) == (7.0, 7.0, 7.0)
