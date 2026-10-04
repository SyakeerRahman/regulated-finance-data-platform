"""Read-only views of the lake for the Data tab: one page of a table, and one transaction followed
through every layer.

Filters run in pyarrow before any row reaches pandas, so a page of bronze reads only the batch
it shows. Nothing here writes.
"""

import json

import pyarrow as pa
import pyarrow.compute as pc
from deltalake import DeltaTable

from finplat.catalog import CATALOG
from finplat.ops import LAKE_TABLES
from finplat.pipeline import BRONZE, GOLD, LABELS, QUARANTINE, SILVER
from finplat.quality import QUALITY

# Newest first, by the column that says when a row arrived in that table.
ORDER = {
    BRONZE: "ingested_at",
    QUARANTINE: "ts",
    SILVER: "ts",
    LABELS: "labelled_at",
    GOLD: "ts",
    QUALITY: "checked_at",
}
SEARCHABLE = ("transaction_id", "account_id")
MAX_PAGE = 200
# The layers a transaction passes through, in order. Quality holds checks, not transactions.
TRACE_TABLES = [BRONZE, QUARANTINE, SILVER, LABELS, GOLD]


def batches(lake: str, table: str) -> list[list]:
    """Each batch in a partitioned table with its row count, newest first. Read from the log."""
    files = pa.table(DeltaTable(f"{lake}/{table}").get_add_actions(flatten=True)).to_pandas()
    if "partition.batch_id" not in files:
        return []
    counts = files.groupby("partition.batch_id")["num_records"].sum()
    return [[batch, int(rows)] for batch, rows in sorted(counts.items(), reverse=True)]


def page(
    lake: str, table: str, *, batch: str | None = None, text: str | None = None, limit: int = 50, offset: int = 0
) -> dict:
    """One page of a table, newest first, with the number of rows that match in total."""
    if table not in LAKE_TABLES:
        raise ValueError(f"unknown table {table}")
    delta = DeltaTable(f"{lake}/{table}")
    dataset = delta.to_pyarrow_dataset()
    names = dataset.schema.names

    conditions = []
    if batch:
        conditions.append(pc.field("batch_id") == batch)
    if text:
        matches = [
            pc.match_substring(_text(name), text.strip(), ignore_case=True) for name in SEARCHABLE if name in names
        ]
        if matches:
            either = matches[0]
            for match in matches[1:]:
                either = either | match
            conditions.append(either)
    condition = None
    for part in conditions:
        condition = part if condition is None else condition & part

    rows = dataset.to_table(filter=condition)
    limit = max(1, min(limit, MAX_PAGE))
    offset = max(0, offset)
    order = ORDER.get(table)
    if order in names:
        # Only the rows up to the end of this page, not a sort of the whole table. Bronze grows
        # by 180,000 rows an hour while the feed runs at 50 a second.
        wanted = min(offset + limit, rows.num_rows)
        top = pc.select_k_unstable(rows, k=wanted, sort_keys=[(order, "descending")]) if wanted else []
        shown = rows.take(top).sort_by([(order, "descending")]).slice(offset, limit)
    else:
        shown = rows.slice(offset, limit)

    return {
        "table": table,
        "version": delta.version(),
        "columns": [
            {"name": field.name, "type": str(field.type), "description": CATALOG[table].columns.get(field.name, "")}
            for field in rows.schema
        ],
        "rows": _records(shown),
        "total": rows.num_rows,
    }


def trace(lake: str, transaction_id: str) -> dict:
    """Every row with this transaction_id, in each layer it passes through."""
    layers = {}
    for table in TRACE_TABLES:
        try:
            dataset = DeltaTable(f"{lake}/{table}").to_pyarrow_dataset()
        except Exception:  # noqa: BLE001 - a missing table shows as a layer with no rows
            layers[table] = []
            continue
        layers[table] = _records(dataset.to_table(filter=_text("transaction_id") == transaction_id))
    return {"transaction_id": transaction_id, "layers": layers}


def _text(name: str):
    """A column as plain strings. Some writes store text as string_view, which pyarrow will not
    compare with a plain string."""
    return pc.field(name).cast(pa.string())


def _records(table) -> list[dict]:
    """JSON-safe rows: timestamps as ISO text and NaN as null, which the browser can parse."""
    return json.loads(table.to_pandas().to_json(orient="records", date_format="iso"))
