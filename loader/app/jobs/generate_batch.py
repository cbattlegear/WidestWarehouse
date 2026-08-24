from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .. import db, discovery
from ..etl_control import BatchContext


def _value(column: discovery.ColumnInfo, rng: random.Random, row: int, batch_id: int) -> Any:
    value = _raw_value(column, rng, row, batch_id)
    # Generated text must fit the target column or the whole batch fails on truncation.
    if isinstance(value, str) and column.max_length and column.max_length > 0:
        if len(value) > column.max_length:
            value = value[: column.max_length]
    return value


TEXT_TYPES = {"char", "nchar", "varchar", "nvarchar", "text", "ntext"}

INT_MAX_BY_TYPE = {"tinyint": 255, "smallint": 32767, "int": 100000, "bigint": 100000}


def _decimal_value(column: discovery.ColumnInfo, rng: random.Random) -> float:
    """Stay inside the column's precision/scale; decimal(5,4) tops out at 9.9999."""
    if column.data_type in {"float", "real"}:
        return round(rng.uniform(1, 10000), 4)
    precision = int(column.precision or 18)
    scale = int(column.scale if column.scale is not None else 4)
    integer_digits = max(precision - scale, 1)
    upper = min(10 ** integer_digits - 1, 10000)
    return round(rng.uniform(0, upper) if upper > 0 else 0.0, min(scale, 4))


def _raw_value(column: discovery.ColumnInfo, rng: random.Random, row: int, batch_id: int) -> Any:
    name = column.name
    dtype = column.data_type
    if name == "BatchId":
        return batch_id
    if name == "RowNumber":
        return row
    if name == "RowHash":
        return hashlib.sha256(f"{batch_id}:{row}:{name}".encode()).digest()
    if name.endswith("DateKey"):
        return int((date.today() - timedelta(days=rng.randint(0, 365))).strftime("%Y%m%d"))
    if name.endswith("TimeOfDayKey"):
        return rng.randint(0, 86399)
    # Name-based heuristics only apply to text columns; a column such as SequenceNumber
    # is an int and must not receive a formatted string.
    if dtype in TEXT_TYPES:
        if name.endswith("Number") or name.endswith("Code") or name.endswith("Id"):
            return f"{name[:12].upper()}-{rng.randint(1, max(100, row + batch_id % 1000)):06d}"
        if name.endswith("Name") or "Description" in name:
            return f"{name} {rng.randint(1, 999999)}"
        return f"{name}_{rng.randint(1, 999999)}"
    if dtype in {"int", "bigint", "smallint", "tinyint"}:
        return rng.randint(1, INT_MAX_BY_TYPE.get(dtype, 100000))
    if dtype == "bit":
        return rng.randint(0, 1)
    if dtype in {"decimal", "numeric", "money", "float", "real"}:
        return _decimal_value(column, rng)
    if dtype == "date":
        return (date.today() - timedelta(days=rng.randint(0, 365))).isoformat()
    if dtype.startswith("datetime"):
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="milliseconds")
    if dtype.startswith("time"):
        return f"{rng.randint(0,23):02d}:{rng.randint(0,59):02d}:00"
    if dtype == "uniqueidentifier":
        return f"00000000-0000-0000-0000-{rng.randint(0, 999999999999):012d}"
    if dtype.startswith("binary") or dtype.startswith("varbinary"):
        return hashlib.sha256(f"{batch_id}:{row}:{name}".encode()).digest()
    return f"{name}_{rng.randint(1, 999999)}"


def _key_pools(conn, table: str, column_names: set[str]) -> dict[str, list[Any]]:
    """Sample real surrogate keys for staged FK columns so generated batches are
    referentially plausible instead of random integers."""
    if not discovery.table_exists(conn, "fact", table):
        return {}
    pools: dict[str, list[Any]] = {}
    for fk in discovery.fact_foreign_keys(conn, table):
        if fk.child_column not in column_names or fk.child_column in pools:
            continue
        sql = (
            f"SELECT TOP 500 {db.quote_name(fk.parent_column)} AS K "
            f"FROM {db.quote_name(fk.parent_schema)}.{db.quote_name(fk.parent_table)} "
            f"WHERE {db.quote_name(fk.parent_column)} <> -1"
        )
        values = [r["K"] for r in db.query(conn, sql)]
        if values:
            pools[fk.child_column] = values
    return pools


def run(ctx: BatchContext, conn) -> dict[str, int]:
    stg_tables = discovery.list_tables(conn, "stg") if discovery.schema_exists(conn, "stg") else []
    if not stg_tables:
        ctx.logger.warning("no_staging_tables", batch_id=ctx.batch_id, job_name=ctx.job_name)
        return {}
    batch_dir = Path(ctx.config.landing_dir) / str(ctx.batch_id)
    batch_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(ctx.batch_id)
    counts: dict[str, int] = {}
    per_table = max(1, ctx.config.batch_rows_per_cycle // max(1, len(stg_tables)))
    for table in stg_tables:
        columns = discovery.get_columns(conn, "stg", table)
        pools = _key_pools(conn, table, {c.name for c in columns})
        rows = [
            {
                col.name: rng.choice(pools[col.name]) if col.name in pools else _value(col, rng, i + 1, ctx.batch_id)
                for col in columns
            }
            for i in range(per_table)
        ]
        path = batch_dir / f"stg.{table}.csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        counts[f"stg.{table}.csv"] = len(rows)
    ctx.row_counts.update(counts)
    return counts
