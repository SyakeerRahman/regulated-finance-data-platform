"""The live feed's payments. Each one must be new to the alert store, for as long as the service runs."""

from datetime import UTC, date, datetime

import pandas as pd

from finplat.feed import Pool
from finplat.generate import generate


class Clock:
    def __init__(self, day: date, hour: int = 12) -> None:
        self.day, self.hour = day, hour

    def __call__(self) -> datetime:
        return datetime(self.day.year, self.day.month, self.day.day, self.hour, tzinfo=UTC)


def take(pool: Pool, count: int) -> list[str]:
    return [pool.take()["transaction_id"] for _ in range(count)]


def test_the_first_round_is_the_daily_batch():
    """Round 0 shares its ids with the batch, so the Data tab can trace a live alert through every layer."""
    # 3,000 rows, so the noon hour of round 0 holds more than the 50 taken.
    pool = Pool(seed=7, accounts=500, rows=3_000, now=Clock(date(2026, 10, 3)))
    batch, _ = generate(date(2026, 10, 3), 3_000, 7, 500)

    assert set(take(pool, 50)) <= set(batch["transaction_id"])
    assert pool.round == 0


def test_no_id_repeats_when_a_round_runs_out():
    """Seen live: one pool replayed in a loop, and after 5.5 hours every alert was a duplicate."""
    pool = Pool(seed=7, accounts=500, rows=300, now=Clock(date(2026, 10, 3)))

    ids = take(pool, 1_000)

    assert pool.round >= 3
    assert len(ids) == len(set(ids))


def test_a_new_day_starts_again_at_round_zero():
    clock = Clock(date(2026, 10, 3))
    pool = Pool(seed=7, accounts=500, rows=300, now=clock)
    take(pool, 400)

    clock.day = date(2026, 10, 4)
    first = pool.take()["transaction_id"]

    assert pool.round == 0
    assert first.startswith("20261004-") and "-r" not in first


def test_a_later_round_is_the_same_on_every_run():
    """A round can be rebuilt from its id alone, which is how the AI evaluation finds the true labels."""
    first, _ = generate(date(2026, 10, 3), 300, 7, 500, round_=2)
    again, _ = generate(date(2026, 10, 3), 300, 7, 500, round_=2)

    assert first.equals(again)
    assert first["transaction_id"].str.startswith("20261003-r2-").all()


def test_each_row_comes_from_the_hour_on_the_clock():
    """The feed stamps the clock on each row. A daytime row stamped 02:00 is a payment that exists
    nowhere in training, and the drift check read it as drift (SCRUM-49)."""
    clock = Clock(date(2026, 10, 3), hour=2)
    pool = Pool(seed=7, accounts=500, rows=2_000, now=clock)
    night = [pool.take() for _ in range(50)]
    clock.hour = 14
    day = [pool.take() for _ in range(50)]

    assert {pd.Timestamp(row["ts"]).hour for row in night} == {2}
    assert {pd.Timestamp(row["ts"]).hour for row in day} == {14}
