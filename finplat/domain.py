"""The vocabulary of a card transaction.

This module imports nothing. The Lambda package carries it, and a Lambda zip has 250 MB unzipped
to spend, which numpy and pandas would take on their own.
"""

HOME_COUNTRY = "MY"
ABROAD = ["SG", "TH", "ID", "GB", "US", "NG"]
CATEGORIES = ["grocery", "fuel", "dining", "travel", "electronics", "online_gaming", "jewellery"]
RISKY_CATEGORIES = {"electronics", "online_gaming", "jewellery"}
CHANNELS = ["chip", "contactless", "online"]

# The order is part of the contract. A model reads a positional matrix, so a swapped pair of
# columns is silent and wrong.
FEATURE_COLUMNS = [
    "amount",
    "hour",
    "is_night",
    "is_abroad",
    "is_online",
    "is_risky_category",
    "amount_vs_account",
    "prior_transactions",
]
