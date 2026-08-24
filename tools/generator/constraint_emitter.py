from __future__ import annotations

from pathlib import Path

from .ddl_emitter import HEADER, q
from .graph import topological_sort
from .model_loader import Model, Table


def fk_name(child: Table, col: str) -> str:
    return f"FK_{child.schema}_{child.name}_{col}"


def constraints_for(table: Table, model: Model) -> list[str]:
    by_schema = model.by_schema_name()
    statements: list[str] = []
    for fk in sorted(table.foreign_keys, key=lambda f: (f.parent_schema, f.parent_table, f.column_name)):
        parent = by_schema.get((fk.parent_schema, fk.parent_table))
        if not parent:
            continue
        name = fk_name(table, fk.column_name)
        statements.append(
            f"IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'{name}' AND parent_object_id = OBJECT_ID(N'{table.schema}.{table.name}'))\n"
            "BEGIN\n"
            f"    ALTER TABLE {q(table.schema)}.{q(table.name)} WITH CHECK ADD CONSTRAINT {q(name)} FOREIGN KEY ({q(fk.column_name)}) REFERENCES {q(parent.schema)}.{q(parent.name)} ({q(parent.key_column)});\n"
            f"    ALTER TABLE {q(table.schema)}.{q(table.name)} CHECK CONSTRAINT {q(name)};\n"
            "END\nGO\n"
        )
    return statements


def emit_constraints(model: Model, out: Path) -> list[Path]:
    groups: dict[str, list[str]] = {}
    for table in topological_sort(model):
        stmts = constraints_for(table, model)
        if stmts:
            groups.setdefault(table.subject_area, []).extend(stmts)
    written: list[Path] = []
    for idx, subject in enumerate(sorted(groups), 10):
        path = out / "50_constraints" / f"{idx:02d}_{subject}.sql"
        path.write_text(HEADER + f"-- Subject area: {subject}\n\n" + "\n".join(groups[subject]), encoding="utf-8", newline="\n")
        written.append(path)
    return written
