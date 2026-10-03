"""The payments the live feed replays.

The first round of a day is that day's batch, shuffled. When it runs out, the next round draws
new rows of the same day with ids of their own. A new UTC day starts again at round 0.

One fixed pool replayed in a loop was the first design. After 20,000 payments, about 5.5 hours at
one a second, every id repeated, the alert store ignored each one as a replay, and the Alerts tab
stopped moving for the rest of the life of the process.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime

from finplat.generate import generate

POOL_ROWS = 20_000


def _utc_today() -> date:
    return datetime.now(UTC).date()


class Pool:
    def __init__(self, seed: int, accounts: int, rows: int = POOL_ROWS, today: Callable[[], date] = _utc_today) -> None:
        self.seed = seed
        self.accounts = accounts
        self.rows = rows
        self.today = today
        self.day: date | None = None
        self.round = 0
        self._rows: list[dict] = []
        self._next = 0

    def take(self) -> dict:
        day = self.today()
        if day != self.day:
            self._fill(day, 0)
        elif self._next >= len(self._rows):
            self._fill(day, self.round + 1)
        row = self._rows[self._next]
        self._next += 1
        return dict(row)

    def _fill(self, day: date, round_: int) -> None:
        transactions, _ = generate(day, self.rows, self.seed, self.accounts, round_=round_)
        # The dirty rows are for the batch path, which must refuse and remove them. The feed shows
        # scoring, and a repeated row would only be an alert the store throws away.
        clean = transactions[transactions["account_id"].notna() & (transactions["amount"] > 0)]
        clean = clean.drop_duplicates("transaction_id")
        # Shuffled, because the feed stamps its own clock on each row. In time order a round
        # opens on the small hours, where fraud concentrates, and the first minute is all alerts.
        self._rows = clean.sample(frac=1, random_state=self.seed + round_).to_dict("records")
        self.day, self.round, self._next = day, round_, 0
