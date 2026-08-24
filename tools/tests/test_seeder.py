from __future__ import annotations

import csv
import filecmp
from pathlib import Path

from tools.generator.ddl_emitter import table_columns
from tools.generator.model_loader import load_model
from tools.seeder.seed_emitter import DATE_END, DATE_START, generate_seed
from tools.seeder.value_factories import max_length, type_info

FIXTURE_MODEL = Path(__file__).parent / "fixtures" / "model"


def _read_csv(path: Path):
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_seed_generation_is_deterministic(tmp_path: Path):
    out1, sql1 = tmp_path / "seed1", tmp_path / "sql1"
    out2, sql2 = tmp_path / "seed2", tmp_path / "sql2"
    generate_seed(FIXTURE_MODEL, out1, sql1, scale=0.001)
    generate_seed(FIXTURE_MODEL, out2, sql2, scale=0.001)
    files1 = sorted(p.relative_to(out1) for p in out1.rglob("*.csv"))
    assert files1 == sorted(p.relative_to(out2) for p in out2.rglob("*.csv"))
    for rel in files1:
        assert filecmp.cmp(out1 / rel, out2 / rel, shallow=False), rel


def test_unknown_members_exist_for_ref_dim_bridge(tmp_path: Path):
    out, sql = tmp_path / "seed", tmp_path / "sql"
    generate_seed(FIXTURE_MODEL, out, sql, scale=0.001)
    model = load_model(FIXTURE_MODEL)
    for table in model.tables:
        if table.kind not in {"ref", "dim", "bridge"}:
            continue
        rows = _read_csv(out / table.schema / f"{table.name}.csv")
        assert rows[0][table.key_column] == "-1", table.name


def test_seed_fk_values_reference_generated_parent_keys(tmp_path: Path):
    out, sql = tmp_path / "seed", tmp_path / "sql"
    result = generate_seed(FIXTURE_MODEL, out, sql, scale=0.001)
    model = load_model(FIXTURE_MODEL)
    for table in model.tables:
        if table.kind == "etl" or not table.foreign_keys:
            continue
        rows = _read_csv(out / table.schema / f"{table.name}.csv")
        for fk in table.foreign_keys:
            parent_keys = {str(v) for v in result.key_sets[(fk.parent_schema, fk.parent_table)]}
            for row in rows:
                value = row[fk.column_name]
                assert value in parent_keys, (table.name, fk.column_name, value)


def test_dim_date_count_and_key_format(tmp_path: Path):
    out, sql = tmp_path / "seed", tmp_path / "sql"
    generate_seed(FIXTURE_MODEL, out, sql, scale=0.001)
    rows = _read_csv(out / "dim" / "Date.csv")
    assert len(rows) == (DATE_END - DATE_START).days + 2  # includes -1 unknown
    assert rows[0]["DateKey"] == "-1"
    for row in rows[1:]:
        key = row["DateKey"]
        assert len(key) == 8 and key.isdigit()


def test_nvarchar_and_char_lengths_are_respected(tmp_path: Path):
    out, sql = tmp_path / "seed", tmp_path / "sql"
    generate_seed(FIXTURE_MODEL, out, sql, scale=0.001)
    model = load_model(FIXTURE_MODEL)
    for table in model.tables:
        if table.kind == "stg":
            continue
        path = out / table.schema / f"{table.name}.csv"
        if not path.exists():
            continue
        limited = {c.name: max_length(c.type) for c in table_columns(table) if type_info(c.type)[0] in {"nvarchar", "char"} and max_length(c.type) is not None}
        if not limited:
            continue
        for row in _read_csv(path):
            for name, limit in limited.items():
                assert len(row[name]) <= limit, (table.name, name, row[name])
