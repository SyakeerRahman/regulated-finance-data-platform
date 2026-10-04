from datetime import date

import pytest
from deltalake import DeltaTable

from finplat.catalog import CATALOG, describe
from finplat.lake_browser import page
from finplat.ops import LAKE_TABLES
from finplat.pipeline import GOLD, SILVER
from finplat.quality import record, run_checks
from tests.test_quality import load


@pytest.fixture(scope="module")
def lake(tmp_path_factory) -> str:
    path = str(tmp_path_factory.mktemp("lake") / "lake")
    for day in (date(2026, 9, 1), date(2026, 9, 2)):
        record(path, run_checks(path, load(path, day, rows=1_000)))
    return path


def columns(lake: str, table: str) -> set[str]:
    return set(DeltaTable(f"{lake}/{table}").to_pyarrow_dataset().schema.names)


def test_every_lake_table_is_in_the_catalog():
    assert set(CATALOG) == set(LAKE_TABLES)


@pytest.mark.parametrize("table", LAKE_TABLES)
def test_every_column_has_a_description(lake, table):
    missing = columns(lake, table) - set(CATALOG[table].columns)
    assert not missing, f"{table} has columns with no catalog entry: {sorted(missing)}"
    assert all(text.strip() for text in CATALOG[table].columns.values())


@pytest.mark.parametrize("table", LAKE_TABLES)
def test_the_catalog_names_no_column_the_table_lacks(lake, table):
    extra = set(CATALOG[table].columns) - columns(lake, table)
    assert not extra, f"the catalog describes columns that {table} does not have: {sorted(extra)}"


def test_gold_retention_follows_silver():
    assert describe(GOLD)["retention"] == f"Follows silver: {describe(SILVER)['retention']}"


def test_the_data_tab_gets_each_column_description(lake):
    shown = {column["name"]: column["description"] for column in page(lake, GOLD, limit=1)["columns"]}
    assert shown["amount_vs_account"] == CATALOG[GOLD].columns["amount_vs_account"]
