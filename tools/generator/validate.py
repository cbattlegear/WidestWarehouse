from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from .graph import find_cycles
from .model_loader import Column, Model, Table

RESERVED_WORDS = {
    "ADD", "ALTER", "AND", "AS", "ASC", "AUTHORIZATION", "BACKUP", "BEGIN", "BETWEEN", "BREAK",
    "BROWSE", "BULK", "BY", "CASCADE", "CASE", "CHECK", "CHECKPOINT", "CLOSE", "CLUSTERED",
    "COALESCE", "COLLATE", "COLUMN", "COMMIT", "COMPUTE", "CONSTRAINT", "CONTAINS", "CONVERT",
    "CREATE", "CROSS", "CURRENT", "CURRENT_DATE", "CURRENT_TIME", "CURRENT_TIMESTAMP", "DATABASE",
    "DBCC", "DEALLOCATE", "DECLARE", "DEFAULT", "DELETE", "DENY", "DESC", "DISK", "DISTINCT",
    "DISTRIBUTED", "DOUBLE", "DROP", "ELSE", "END", "ERRLVL", "ESCAPE", "EXCEPT", "EXEC",
    "EXECUTE", "EXISTS", "EXIT", "EXTERNAL", "FETCH", "FILE", "FILLFACTOR", "FOR", "FOREIGN",
    "FREETEXT", "FROM", "FULL", "FUNCTION", "GOTO", "GRANT", "GROUP", "HAVING", "HOLDLOCK",
    "IDENTITY", "IDENTITY_INSERT", "IDENTITYCOL", "IF", "IN", "INDEX", "INNER", "INSERT",
    "INTERSECT", "INTO", "IS", "JOIN", "KEY", "KILL", "LEFT", "LIKE", "LINENO", "LOAD",
    "MERGE", "NATIONAL", "NOCHECK", "NONCLUSTERED", "NOT", "NULL", "NULLIF", "OF", "OFF",
    "OFFSETS", "ON", "OPEN", "OPENDATASOURCE", "OPENQUERY", "OPENROWSET", "OPENXML", "OPTION",
    "OR", "ORDER", "OUTER", "OVER", "PERCENT", "PIVOT", "PLAN", "PRECISION", "PRIMARY",
    "PRINT", "PROC", "PROCEDURE", "PUBLIC", "RAISERROR", "READ", "READTEXT", "RECONFIGURE",
    "REFERENCES", "REPLICATION", "RESTORE", "RESTRICT", "RETURN", "REVERT", "REVOKE", "RIGHT",
    "ROLLBACK", "ROWCOUNT", "ROWGUIDCOL", "RULE", "SAVE", "SCHEMA", "SECURITYAUDIT", "SELECT",
    "SEMANTICKEYPHRASETABLE", "SEMANTICSIMILARITYDETAILSTABLE", "SEMANTICSIMILARITYTABLE",
    "SESSION_USER", "SET", "SETUSER", "SHUTDOWN", "SOME", "STATISTICS", "SYSTEM_USER", "TABLE",
    "TABLESAMPLE", "TEXTSIZE", "THEN", "TO", "TOP", "TRAN", "TRANSACTION", "TRIGGER",
    "TRUNCATE", "TRY_CONVERT", "TSEQUAL", "UNION", "UNIQUE", "UNPIVOT", "UPDATE", "UPDATETEXT",
    "USE", "USER", "VALUES", "VARYING", "VIEW", "WAITFOR", "WHEN", "WHERE", "WHILE", "WITH",
    "WITHIN GROUP", "WRITETEXT",
}

TYPE_RE = re.compile(
    r"^(int|bigint|smallint|tinyint|bit|money|float|date|uniqueidentifier|nvarchar\((?:[1-9]\d*|max)\)|char\([1-9]\d*\)|varbinary\([1-9]\d*\)|binary\([1-9]\d*\)|decimal\([1-9]\d*,[0-9]\d*\)|time\([0-7]\)|datetime2\([0-7]\))$"
)


@dataclass
class ValidationError:
    source_file: str
    table: str
    message: str

    def __str__(self) -> str:
        return f"{self.source_file} [{self.table}]: {self.message}"


def _all_columns(table: Table) -> list[Column]:
    cols: list[Column] = []
    if table.kind in {"ref", "dim", "bridge"}:
        if table.explicit_key and table.business_key:
            cols.append(table.business_key)
        elif table.kind in {"ref", "dim", "bridge"}:
            key_type = "int"
            cols.append(Column(f"{table.name}Key", key_type, nullable=False, identity=True))
        if table.business_key and not table.explicit_key:
            cols.append(table.business_key)
        cols.extend(Column(fk.column_name, "int", nullable=fk.nullable) for fk in table.foreign_keys)
        cols.extend(table.columns)
        cols.extend(_audit_columns(table))
        if table.scd == "type2":
            cols.extend(_scd2_columns())
    elif table.kind == "fact":
        cols.append(Column(f"{table.name}Key", "bigint", nullable=False, identity=True))
        cols.extend(Column(fk.column_name, "int", nullable=fk.nullable, default="-1") for fk in table.foreign_keys)
        cols.extend(table.columns)
        if table.fact_type == "accumulating_snapshot":
            cols.append(Column(f"{table.name}LagDays", "int", nullable=True))
        cols.extend(_audit_columns(table))
    else:
        cols.extend(table.columns)
    return cols


def _audit_columns(table: Table | None = None) -> list[Column]:
    cols = [
        Column("BatchId", "bigint", nullable=False, default="0"),
        Column("LoadedAtUtc", "datetime2(3)", nullable=False, default="SYSUTCDATETIME()"),
    ]
    if table is None or table.name != "SourceSystem":
        cols.insert(0, Column("SourceSystemKey", "int", nullable=False, default="-1"))
    return cols


def _scd2_columns() -> list[Column]:
    return [
        Column("EffectiveFromUtc", "datetime2(3)", nullable=False),
        Column("EffectiveToUtc", "datetime2(3)", nullable=False, default="'9999-12-31'"),
        Column("IsCurrent", "bit", nullable=False, default="1"),
        Column("RowHash", "binary(32)", nullable=False),
    ]


def validate_model(model: Model) -> list[ValidationError]:
    errors: list[ValidationError] = []
    by_name: dict[str, list[Table]] = {}
    by_schema_name = model.by_schema_name()
    single_by_name = model.by_name()
    for table in model.tables:
        by_name.setdefault(table.name, []).append(table)
    for name, tables in sorted(by_name.items()):
        if len(tables) > 1:
            locs = ", ".join(sorted(t.source_file for t in tables))
            errors.append(ValidationError(locs, name, "duplicate table name across model"))

    for table in sorted(model.tables, key=lambda t: (t.source_file, t.name)):
        if table.name.upper() in RESERVED_WORDS:
            errors.append(ValidationError(table.source_file, table.name, "reserved T-SQL word used as table name"))
        if len(table.name) > 116:
            errors.append(ValidationError(table.source_file, table.name, "table identifier longer than 116 characters"))

        expected_width = (1 if table.business_key else 0) + len(table.columns)
        for i, row in enumerate(table.seed_values, 1):
            if len(row) != expected_width:
                errors.append(ValidationError(table.source_file, table.name, f"seed_values row {i} has width {len(row)}; expected {expected_width}"))

        seen_cols: dict[str, str] = {}
        for col in _all_columns(table):
            key = col.name.lower()
            if col.name.upper() in RESERVED_WORDS:
                errors.append(ValidationError(table.source_file, table.name, f"reserved T-SQL word used as column name: {col.name}"))
            if len(col.name) > 116:
                errors.append(ValidationError(table.source_file, table.name, f"column identifier longer than 116 characters: {col.name}"))
            if not TYPE_RE.match(col.type.lower()):
                errors.append(ValidationError(table.source_file, table.name, f"unknown column type for {col.name}: {col.type}"))
            if key in seen_cols:
                errors.append(ValidationError(table.source_file, table.name, f"duplicate column after injection: {col.name}"))
            seen_cols[key] = col.name

        for fk in table.foreign_keys:
            target = by_schema_name.get((fk.parent_schema, fk.parent_table))
            if target is None:
                wrong = single_by_name.get(fk.parent_table)
                if wrong is not None:
                    errors.append(ValidationError(table.source_file, table.name, f"FK {fk.column_name} targets wrong schema class {wrong.schema}; expected {fk.parent_schema}"))
                else:
                    errors.append(ValidationError(table.source_file, table.name, f"FK {fk.column_name} targets missing {fk.parent_schema}.{fk.parent_table}"))
            elif target.schema != fk.parent_schema:
                errors.append(ValidationError(table.source_file, table.name, f"FK {fk.column_name} targets wrong schema class {target.schema}; expected {fk.parent_schema}"))

    for cycle in find_cycles(model):
        errors.append(ValidationError("<model>", " -> ".join(cycle), "cycle in FK graph"))
    return errors


def validate_or_exit(model: Model) -> None:
    errors = validate_model(model)
    if errors:
        print("Validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(1)
