from __future__ import annotations

from pathlib import Path

from .ddl_emitter import HEADER, column_sql, q
from .model_loader import Model, Table, staging_columns


def _staging_table_sql(table: Table, model: Model) -> str:
    cols = [f"    {column_sql(c)}" for c in staging_columns(table, model)]
    return (
        f"IF OBJECT_ID(N'stg.{table.name}', N'U') IS NULL\n"
        "BEGIN\n"
        f"CREATE TABLE {q('stg')}.{q(table.name)} (\n"
        + ",\n".join(cols)
        + "\n);\nEND\nGO\n"
    )


def emit_staging(model: Model, out: Path) -> list[Path]:
    stg_tables = [t for t in model.tables if (t.kind == "dim" and t.scd == "type2") or t.kind in {"bridge", "fact"}]
    groups: dict[str, list[str]] = {}
    for table in sorted(stg_tables, key=lambda t: (t.subject_area, t.schema, t.name)):
        groups.setdefault(table.subject_area, []).append(_staging_table_sql(table, model))
    written: list[Path] = []
    for idx, subject in enumerate(sorted(groups), 10):
        path = out / "90_staging" / f"{idx:02d}_{subject}.sql"
        path.write_text(HEADER + f"-- Subject area: {subject}\n\n" + "\n".join(groups[subject]), encoding="utf-8", newline="\n")
        written.append(path)
    return written
