from __future__ import annotations

from pathlib import Path

from .ddl_emitter import HEADER, q
from .model_loader import Model, Table


def _ix_name(prefix: str, table: Table, cols: list[str]) -> str:
    return f"{prefix}_{table.name}_{''.join(cols)}"


def _create_index(table: Table, name: str, cols: list[str], unique: bool = False, columnstore: bool = False) -> str:
    guard = f"IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'{name}' AND object_id = OBJECT_ID(N'{table.schema}.{table.name}'))"
    if columnstore:
        stmt = f"CREATE CLUSTERED COLUMNSTORE INDEX {q(name)} ON {q(table.schema)}.{q(table.name)};"
    else:
        uniq = "UNIQUE " if unique else ""
        stmt = f"CREATE {uniq}NONCLUSTERED INDEX {q(name)} ON {q(table.schema)}.{q(table.name)} ({', '.join(q(c) for c in cols)});"
    return f"{guard}\n    {stmt}\nGO\n"


def indexes_for(table: Table) -> list[str]:
    statements: list[str] = []
    if table.business_key and table.kind in {"ref", "dim"}:
        statements.append(_create_index(table, _ix_name("UX", table, [table.business_key.name]), [table.business_key.name], unique=True))
    if table.kind == "bridge" and table.unique_members:
        member_cols = [fk.column_name for fk in table.foreign_keys if fk.source_kind == "members"]
        if member_cols:
            statements.append(_create_index(table, _ix_name("UX", table, member_cols), member_cols, unique=True))
    if table.kind == "fact":
        for fk in sorted(table.foreign_keys, key=lambda f: f.column_name):
            statements.append(_create_index(table, _ix_name("IX", table, [fk.column_name]), [fk.column_name]))
        if table.columnstore:
            statements.append(_create_index(table, _ix_name("IX", table, ["Columnstore"]), [], columnstore=True))
    return statements


def emit_indexes(model: Model, out: Path) -> list[Path]:
    groups: dict[str, list[str]] = {}
    for table in sorted(model.tables, key=lambda t: (t.subject_area, t.schema, t.name)):
        stmts = indexes_for(table)
        if stmts:
            groups.setdefault(table.subject_area, []).extend(stmts)
    written: list[Path] = []
    for idx, subject in enumerate(sorted(groups), 10):
        path = out / "60_indexes" / f"{idx:02d}_{subject}.sql"
        path.write_text(HEADER + f"-- Subject area: {subject}\n\n" + "\n".join(groups[subject]), encoding="utf-8", newline="\n")
        written.append(path)
    return written
