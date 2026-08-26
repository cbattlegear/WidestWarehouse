from __future__ import annotations

import filecmp
import re
import shutil
from pathlib import Path

import pytest

from tools.generator.ddl_emitter import table_columns
from tools.generator.model_loader import load_model
from tools.generator.validate import validate_model
from tools.generator.cli import main


FIXTURE_MODEL = Path(__file__).parent / "fixtures" / "model"


def test_hierarchy_expansion_fk_chain():
    model = load_model(FIXTURE_MODEL)
    tables = model.by_name()
    levels = ["ProductDivision", "ProductLine", "ProductFamily"]
    assert all(level in tables for level in levels)
    assert tables["ProductDivision"].business_key.name == "ProductDivisionCode"
    assert any(c.name == "ProductDivisionName" for c in tables["ProductDivision"].columns)
    assert [fk.parent_table for fk in tables["ProductLine"].foreign_keys] == ["ProductDivision"]
    assert [fk.parent_table for fk in tables["ProductFamily"].foreign_keys] == ["ProductLine"]


def test_validation_catches_duplicate_missing_cycle_bad_type(tmp_path: Path):
    root = tmp_path / "model"
    (root / "common").mkdir(parents=True)
    (root / "subject_areas").mkdir()
    (root / "subject_areas" / "bad.yaml").write_text(
        """
subject_area: bad
dimensions:
  - name: Alpha
    business_key: {name: AlphaCode, type: "nvarchar(20)"}
    columns: [{name: BadAmount, type: "varchar(20)"}]
    parents: [{name: Beta}]
  - name: Beta
    business_key: {name: BetaCode, type: "nvarchar(20)"}
    parents: [{name: Alpha}]
  - name: Alpha
    business_key: {name: OtherCode, type: "nvarchar(20)"}
facts:
  - name: BadFact
    dimensions: [{name: MissingDim}]
""",
        encoding="utf-8",
    )
    messages = "\n".join(str(e) for e in validate_model(load_model(root)))
    assert "duplicate table name" in messages
    assert "MissingDim" in messages
    assert "cycle in FK graph" in messages
    assert "unknown column type" in messages


def test_scd2_columns_injected():
    product = load_model(FIXTURE_MODEL).by_name()["Product"]
    names = [c.name for c in table_columns(product)]
    assert {"EffectiveFromUtc", "EffectiveToUtc", "IsCurrent", "RowHash"}.issubset(names)


def test_fact_fk_columns_default_to_minus_one():
    fact = load_model(FIXTURE_MODEL).by_name()["ProductionOrder"]
    fk_cols = {c.name: c for c in table_columns(fact) if c.name.endswith("Key") and c.name != "ProductionOrderKey"}
    assert fk_cols
    assert all(c.default == "-1" for c in fk_cols.values())


def test_emit_is_deterministic(tmp_path: Path):
    out1 = tmp_path / "sql1"
    out2 = tmp_path / "sql2"
    main(["--model", str(FIXTURE_MODEL), "emit", "--out", str(out1)])
    main(["--model", str(FIXTURE_MODEL), "emit", "--out", str(out2)])
    files1 = sorted(p.relative_to(out1) for p in out1.rglob("*.sql"))
    files2 = sorted(p.relative_to(out2) for p in out2.rglob("*.sql"))
    assert files1 == files2
    for rel in files1:
        assert filecmp.cmp(out1 / rel, out2 / rel, shallow=False), rel


def test_fixture_model_validates():
    assert validate_model(load_model(FIXTURE_MODEL)) == []


def test_emit_preserves_static_folders_and_builds_them_in_order(tmp_path: Path):
    """Hand-written SQL must survive `emit` and still make it into build_all.sql.

    This is the guarantee that makes sql/75_procedures/ safe to hand-author. If emit ever
    starts wiping static folders, six stored procedures disappear silently.
    """
    out = tmp_path / "sql"
    static = out / "75_procedures"
    static.mkdir(parents=True)
    handwritten = static / "10_usp_Example.sql"
    handwritten.write_text("-- hand written\n", encoding="utf-8")

    main(["--model", str(FIXTURE_MODEL), "emit", "--out", str(out)])

    assert handwritten.exists(), "emit deleted hand-written SQL"
    assert handwritten.read_text(encoding="utf-8") == "-- hand written\n"

    build = (out / "build_all.sql").read_text(encoding="utf-8")
    assert ":r ./75_procedures/10_usp_Example.sql" in build

    # Every include must appear in numeric folder order, with 75 slotted in among them
    # rather than appended at the end.
    included = re.findall(r":r \./(\d+)_", build)
    assert "75" in included
    assert included == sorted(included)
