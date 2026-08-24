from __future__ import annotations

from pathlib import Path

from .ddl_emitter import HEADER, q, table_columns
from .model_loader import ForeignKey, Model, Table


def _dim_parents(table: Table) -> list[ForeignKey]:
    return sorted([fk for fk in table.foreign_keys if fk.parent_schema == "dim" and fk.parent_table != table.name], key=lambda f: (f.parent_table, f.column_name))


def _ref_parents(table: Table) -> list[ForeignKey]:
    return sorted([fk for fk in table.foreign_keys if fk.parent_schema == "ref"], key=lambda f: (f.parent_table, f.column_name))


MAX_IDENTIFIER = 128

# SQL Server hard-limits a view to 1024 columns. A flattened snowflake view carries the
# leaf dimension's full attribute set plus only the *identifying* columns of each
# ancestor, which keeps the views useful and well inside the limit.
MAX_VIEW_COLUMNS = 1000


def _ancestor_columns(parent: Table) -> list:
    """Identifying columns only: the business key plus any Code/Name attributes."""
    bk = parent.business_key.name if parent.business_key else None
    picked = [
        c
        for c in table_columns(parent)
        if c.name == bk or c.name.endswith("Name") or c.name.endswith("Code")
    ]
    return picked or table_columns(parent)[:1]


def _role_of(fk: ForeignKey) -> str:
    """Role-played prefix for an FK, e.g. ShipProductKey -> ShipProduct."""
    name = fk.column_name
    return name[:-3] if name.endswith("Key") and len(name) > 3 else name


def _unique(candidate: str, used: set[str]) -> str:
    """Keep output column names unique and within SQL Server's identifier limit."""
    base = candidate[:MAX_IDENTIFIER]
    if base not in used:
        used.add(base)
        return base
    for n in range(2, 1000):
        suffix = f"_{n}"
        trimmed = f"{base[:MAX_IDENTIFIER - len(suffix)]}{suffix}"
        if trimmed not in used:
            used.add(trimmed)
            return trimmed
    raise ValueError(f"cannot uniquify column alias {candidate!r}")


def _flattened_view(table: Table, model: Model) -> str:
    by_schema = model.by_schema_name()
    visited: set[tuple[str, str, str, str]] = set()
    used_names: set[str] = set()
    joins: list[str] = []
    select_cols = [
        f"    b.{q(c.name)} AS {q(_unique(c.name, used_names))}" for c in table_columns(table)
    ]
    counter = 0

    def add_parent(child_alias: str, fk: ForeignKey, prefix: str, walk_dim: bool) -> None:
        nonlocal counter
        parent = by_schema.get((fk.parent_schema, fk.parent_table))
        if parent is None:
            return
        role_prefix = f"{prefix}_{_role_of(fk)}" if prefix else _role_of(fk)
        # Guard against revisiting the same table through the same path (cycle safety).
        key = (parent.schema, parent.name, fk.column_name, prefix)
        if key in visited:
            return
        visited.add(key)
        counter += 1
        alias = f"p{counter}"
        joins.append(
            f"LEFT JOIN {q(parent.schema)}.{q(parent.name)} {alias} "
            f"ON {child_alias}.{q(fk.column_name)} = {alias}.{q(parent.key_column)}"
        )
        for col in _ancestor_columns(parent):
            if len(select_cols) >= MAX_VIEW_COLUMNS:
                return
            out_name = _unique(f"{role_prefix}_{col.name}", used_names)
            select_cols.append(f"    {alias}.{q(col.name)} AS {q(out_name)}")
        for ref_fk in _ref_parents(parent):
            add_parent(alias, ref_fk, role_prefix, False)
        if walk_dim and parent.schema == "dim":
            for parent_fk in _dim_parents(parent):
                add_parent(alias, parent_fk, role_prefix, True)

    for fk in _ref_parents(table):
        add_parent("b", fk, "", False)
    for fk in _dim_parents(table):
        add_parent("b", fk, "", True)

    return (
        f"CREATE OR ALTER VIEW {q('dim')}.{q('vwDim' + table.name)} AS\n"
        "SELECT\n"
        + ",\n".join(select_cols)
        + f"\nFROM {q(table.schema)}.{q(table.name)} b\n"
        + ("\n".join(joins) + "\n" if joins else "")
        + "GO\n"
    )


def emit_views(model: Model, out: Path) -> list[Path]:
    leaves = [t for t in model.tables if t.kind == "dim" and not t.is_hierarchy]
    groups: dict[str, list[str]] = {}
    for table in sorted(leaves, key=lambda t: (t.subject_area, t.name)):
        groups.setdefault(table.subject_area, []).append(_flattened_view(table, model))
    written: list[Path] = []
    for idx, subject in enumerate(sorted(groups), 10):
        path = out / "70_views" / f"{idx:02d}_{subject}.sql"
        path.write_text(HEADER + f"-- Subject area: {subject}\n\n" + "\n".join(groups[subject]), encoding="utf-8", newline="\n")
        written.append(path)
    return written
