"""Synthetic card transactions, so the platform never holds real customer data."""

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd

HOME_COUNTRY = "MY"
ABROAD = ["SG", "TH", "ID", "GB", "US", "NG"]
CATEGORIES = ["grocery", "fuel", "dining", "travel", "electronics", "online_gaming", "jewellery"]
RISKY_CATEGORIES = {"electronics", "online_gaming", "jewellery"}
CHANNELS = ["chip", "contactless", "online"]
ACCOUNTS = 40_000
FRAUD_RATE = 0.015

# Share of rows broken on purpose, so the silver layer has real work to do.
DUPLICATE_RATE = 0.01
MISSING_ACCOUNT_RATE = 0.005
BAD_AMOUNT_RATE = 0.003

# A card scheme gives the holder a fixed window to dispute a charge. Fraud is confirmed somewhere
# inside it; everything else is only known to be clean once the window shuts.
CHARGEBACK_DAYS = (30, 91)
DISPUTE_WINDOW_DAYS = 90


def generate(day: date, rows: int, seed: int, accounts: int = ACCOUNTS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One day of transactions as a card processor would land them, dirty rows included.

    Returns the transactions and their labels as two frames. The truth never rides on the
    transaction, because a bank does not know it for weeks and a column that is present is a
    column a feature can read by accident.
    """
    # Spend levels depend on the seed only, so an account behaves the same way on every day.
    account_level = np.random.default_rng(seed).lognormal(mean=3.5, sigma=0.6, size=accounts)
    rng = np.random.default_rng([seed, day.toordinal()])

    is_fraud = rng.random(rows) < FRAUD_RATE
    account = rng.integers(0, accounts, rows)

    # Fraud spends more, at night, online, abroad, in categories that resell well.
    amount = account_level[account] * rng.lognormal(0.0, 0.5, rows)
    amount = np.where(is_fraud, amount * rng.uniform(3, 12, rows), amount)
    hour = np.where(is_fraud & (rng.random(rows) < 0.6), rng.integers(0, 6, rows), rng.integers(6, 24, rows))
    abroad = rng.random(rows) < np.where(is_fraud, 0.55, 0.05)
    country = np.where(abroad, rng.choice(ABROAD, rows), HOME_COUNTRY)
    risky = rng.random(rows) < np.where(is_fraud, 0.7, 0.1)
    category = np.where(
        risky,
        rng.choice(sorted(RISKY_CATEGORIES), rows),
        rng.choice([c for c in CATEGORIES if c not in RISKY_CATEGORIES], rows),
    )
    channel = np.where(is_fraud & (rng.random(rows) < 0.7), "online", rng.choice(CHANNELS, rows))

    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    ts = pd.Timestamp(midnight) + pd.to_timedelta(hour * 3600 + rng.integers(0, 3600, rows), unit="s")

    frame = pd.DataFrame(
        {
            "transaction_id": [f"{day:%Y%m%d}-{i:07d}" for i in range(rows)],
            "account_id": [f"ACC{a:05d}" for a in account],
            "merchant_category": category,
            "amount": np.round(amount, 2),
            "currency": "MYR",
            "country": country,
            "channel": channel,
            "ts": ts,
            "is_fraud": is_fraud,
        }
    ).sort_values("ts", ignore_index=True)

    labels = _label(frame, rng)
    frame = frame.drop(columns="is_fraud")

    frame["account_id"] = frame["account_id"].astype("string")
    frame.loc[rng.random(rows) < MISSING_ACCOUNT_RATE, "account_id"] = pd.NA
    frame.loc[rng.random(rows) < BAD_AMOUNT_RATE, "amount"] = -frame["amount"]
    duplicates = frame.sample(frac=DUPLICATE_RATE, random_state=rng.integers(1 << 31))
    return pd.concat([frame, duplicates], ignore_index=True), labels


def _label(frame: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """The verdict on each transaction, dated when a bank would really learn it."""
    fraud = frame["is_fraud"].to_numpy()
    delay = np.where(fraud, rng.integers(*CHARGEBACK_DAYS, len(frame)), DISPUTE_WINDOW_DAYS)
    return pd.DataFrame(
        {
            "transaction_id": frame["transaction_id"],
            "is_fraud": fraud,
            "label_source": np.where(fraud, "chargeback", "dispute window closed"),
            "labelled_at": frame["ts"] + pd.to_timedelta(delay, unit="D"),
        }
    )
