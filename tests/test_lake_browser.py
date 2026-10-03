from datetime import date

import pandas as pd
import pytest
from deltalake import DeltaTable

from finplat.lake_browser import MAX_PAGE, batches, page, trace
from finplat.pipeline import BRONZE, GOLD, QUARANTINE, SILVER
from tests.test_quality import load


@pytest.fixture(scope="module")
def lake(tmp_path_factory) -> str:
    path = str(tmp_path_factory.mktemp("lake") / "lake")
    load(path, date(2026, 9, 1), rows=2_000)
    load(path, date(2026, 9, 2), rows=2_000)
    return path


def test_batches_are_listed_newest_first_with_counts(lake):
    listed = batches(lake, BRONZE)
    assert [batch for batch, _ in listed] == ["2026-09-02", "2026-09-01"]
    assert all(rows >= 2_000 for _, rows in listed)
    # Silver is one table merged on transaction_id, not a partition for each batch.
    assert batches(lake, SILVER) == []


def test_a_page_is_newest_first_and_counts_every_match(lake):
    answer = page(lake, BRONZE, batch="2026-09-01", limit=10, offset=5)
    assert len(answer["rows"]) == 10
    assert answer["total"] >= 2_000
    assert {row["batch_id"] for row in answer["rows"]} == {"2026-09-01"}
    stamps = [row["ingested_at"] for row in answer["rows"]]
    assert stamps == sorted(stamps, reverse=True)


def test_search_matches_an_account_without_case(lake):
    account = page(lake, SILVER, limit=1)["rows"][0]["account_id"]
    answer = page(lake, SILVER, text=account.lower(), limit=MAX_PAGE)
    assert answer["total"] >= 1
    assert {row["account_id"] for row in answer["rows"]} == {account}


def test_an_unknown_table_is_refused(lake):
    with pytest.raises(ValueError):
        page(lake, "../../etc", limit=1)


def test_a_rejected_row_is_in_bronze_and_quarantine_but_not_silver(lake):
    rejected = page(lake, QUARANTINE, limit=MAX_PAGE)["rows"]
    # A duplicate is rejected too, and its first copy does reach silver. Pick a row that is not one.
    broken = next(row for row in rejected if "duplicate" not in row["reason"])

    layers = trace(lake, broken["transaction_id"])["layers"]
    assert layers[BRONZE]
    assert layers[QUARANTINE][0]["reason"] == broken["reason"]
    assert layers[SILVER] == []
    assert layers[GOLD] == []


def test_a_clean_row_reaches_gold_once(lake):
    features = DeltaTable(f"{lake}/{GOLD}").to_pandas()
    transaction_id = features["transaction_id"].iloc[0]

    layers = trace(lake, transaction_id)["layers"]
    assert len(layers[SILVER]) == 1
    assert len(layers[GOLD]) == 1
    assert pd.Timestamp(layers[GOLD][0]["ts"]) == pd.Timestamp(features["ts"].iloc[0])
