from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import db


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool
    ordinal: int
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None


@dataclass(frozen=True)
class ForeignKeyInfo:
    child_schema: str
    child_table: str
    child_column: str
    parent_schema: str
    parent_table: str
    parent_column: str


def table_exists(conn, schema: str, table: str) -> bool:
    return bool(
        db.query(
            conn,
            """
            SELECT 1 AS Present
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            """,
            [schema, table],
        )
    )


def schema_exists(conn, schema: str) -> bool:
    return bool(db.query(conn, "SELECT 1 AS Present FROM sys.schemas WHERE name = ?", [schema]))


def list_tables(conn, schema: str) -> list[str]:
    return [
        r["TABLE_NAME"]
        for r in db.query(
            conn,
            """
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
            """,
            [schema],
        )
    ]


def get_columns(conn, schema: str, table: str) -> list[ColumnInfo]:
    rows = db.query(
        conn,
        """
        SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, ORDINAL_POSITION, CHARACTER_MAXIMUM_LENGTH,
               NUMERIC_PRECISION, NUMERIC_SCALE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
        """,
        [schema, table],
    )
    return [
        ColumnInfo(
            r["COLUMN_NAME"],
            str(r["DATA_TYPE"]).lower(),
            r["IS_NULLABLE"] == "YES",
            int(r["ORDINAL_POSITION"]),
            r.get("CHARACTER_MAXIMUM_LENGTH"),
            r.get("NUMERIC_PRECISION"),
            r.get("NUMERIC_SCALE"),
        )
        for r in rows
    ]


def get_column_names(conn, schema: str, table: str) -> list[str]:
    return [c.name for c in get_columns(conn, schema, table)]


def get_business_key(conn, schema: str, table: str) -> str | None:
    rows = db.query(
        conn,
        """
        SELECT TOP (1) c.name AS ColumnName
        FROM sys.schemas s
        JOIN sys.tables t ON t.schema_id = s.schema_id
        JOIN sys.indexes i ON i.object_id = t.object_id AND i.is_unique = 1 AND i.is_primary_key = 0
        JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal = 1
        JOIN sys.columns c ON c.object_id = t.object_id AND c.column_id = ic.column_id
        WHERE s.name = ? AND t.name = ?
        ORDER BY i.name, ic.key_ordinal
        """,
        [schema, table],
    )
    if rows:
        return rows[0]["ColumnName"]
    cols = get_column_names(conn, schema, table)
    for suffix in ("Number", "Code", "Id", "Name"):
        matches = [c for c in cols if c.endswith(suffix) and not c.endswith("Key")]
        if matches:
            return matches[0]
    return None


def table_load_config(conn) -> dict[tuple[str, str], dict[str, Any]]:
    if not table_exists(conn, "etl", "TableLoadConfig"):
        return {}
    columns = {c.name for c in get_columns(conn, "etl", "TableLoadConfig")}
    enabled_col = next((c for c in ("IsEnabled", "Enabled", "IsActive") if c in columns), None)
    sql = "SELECT * FROM etl.TableLoadConfig"
    if enabled_col:
        sql += f" WHERE ISNULL({db.quote_name(enabled_col)}, 1) = 1"
    rows = db.query(conn, sql)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        schema = row.get("TargetSchemaName") or row.get("TargetSchema") or row.get("SchemaName") or row.get("TableSchema")
        table = row.get("TargetTableName") or row.get("TargetTable") or row.get("TableName")
        if schema and table:
            result[(schema, table)] = row
    return result


def fact_foreign_keys(conn, fact_table: str) -> list[ForeignKeyInfo]:
    rows = db.query(
        conn,
        """
        SELECT cs.name AS ChildSchema, ct.name AS ChildTable, cc.name AS ChildColumn,
               ps.name AS ParentSchema, pt.name AS ParentTable, pc.name AS ParentColumn
        FROM sys.foreign_key_columns fkc
        JOIN sys.tables ct ON ct.object_id = fkc.parent_object_id
        JOIN sys.schemas cs ON cs.schema_id = ct.schema_id
        JOIN sys.columns cc ON cc.object_id = ct.object_id AND cc.column_id = fkc.parent_column_id
        JOIN sys.tables pt ON pt.object_id = fkc.referenced_object_id
        JOIN sys.schemas ps ON ps.schema_id = pt.schema_id
        JOIN sys.columns pc ON pc.object_id = pt.object_id AND pc.column_id = fkc.referenced_column_id
        WHERE cs.name = 'fact' AND ct.name = ? AND ps.name IN ('dim', 'ref')
        ORDER BY cc.name
        """,
        [fact_table],
    )
    return [
        ForeignKeyInfo(
            r["ChildSchema"],
            r["ChildTable"],
            r["ChildColumn"],
            r["ParentSchema"],
            r["ParentTable"],
            r["ParentColumn"],
        )
        for r in rows
    ]
