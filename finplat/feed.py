"""The payments the live feed replays.

The first round of a day is that day's batch, shuffled. When it runs out, the next round draws
new rows of the same day with ids of their own. A new UTC day starts again at round 0.

One fixed pool replayed in a loop was the first design. After 20,000 payments, about 5.5 hours at
one a second, every id repeated, the alert store ignored each one as a replay, and the Alerts tab
stopped moving for the rest of the life of the process.

The feed serves rows of the current UTC hour only. It stamps the real clock on each row, and a row
drawn from the whole day would then be a daytime payment flagged as night, a mix that exists
nowhere in training. Before this, the drift check read is_night at a PSI of 11 on a calm day, and
at 02:00 the amount, the country and the score drifted too (SCRUM-49).
"""

from collections.abc import Callable
from datetime import UTC, date, datetime

import pandas as pd

from finplat.generate import generate

POOL_ROWS = 20_000
# A round can hold no row for a quiet hour when the pool is small. Draw new rounds this many times
# before serving any hour, so a test pool of 300 rows cannot loop for ever.
EMPTY_HOUR_ROUNDS = 10


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Pool:
    def __init__(self, seed: int, accounts: int, rows: int = POOL_ROWS, now: Callable[[], datetime] = _utc_now) -> None:
        self.seed = seed
        self.accounts = accounts
        self.rows = rows
        self.now = now
        self.day: date | None = None
        self.round = 0
        self._by_hour: dict[int, list[dict]] = {}

    def take(self) -> dict:
        moment = self.now()
        day, hour = moment.date(), moment.hour
        if day != self.day:
            self._fill(day, 0)
        for _ in range(EMPTY_HOUR_ROUNDS):
            if self._by_hour.get(hour):
                return dict(self._by_hour[hour].pop())
            # This hour is used up. The rest of the round is dropped: a new round has new ids, so
            # nothing repeats, and the hours that are left would only go stale.
            self._fill(day, self.round + 1)
        return dict(next(rows for rows in self._by_hour.values() if rows).pop())

    def _fill(self, day: date, round_: int) -> None:
        transactions, _ = generate(day, self.rows, self.seed, self.accounts, round_=round_)
        # The dirty rows are for the batch path, which must refuse and remove them. The feed shows
        # scoring, and a repeated row would only be an alert the store throws away.
        clean = transactions[transactions["account_id"].notna() & (transactions["amount"] > 0)]
        clean = clean.drop_duplicates("transaction_id")
        # Shuffled inside each hour, so an hour does not replay in the order of its timestamps.
        shuffled = clean.sample(frac=1, random_state=self.seed + round_)
        hours = pd.to_datetime(shuffled["ts"], utc=True).dt.hour
        self._by_hour = {int(hour): rows.to_dict("records") for hour, rows in shuffled.groupby(hours)}
        self.day, self.round = day, round_
