from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = True
    default: str | None = None
    identity: bool = False
    aggregation: str | None = None


@dataclass(frozen=True)
class ForeignKey:
    parent_table: str
    parent_schema: str
    column_name: str
    role: str | None = None
    nullable: bool = False
    source_kind: str = "parent"


@dataclass
class Table:
    name: str
    schema: str
    kind: str
    subject_area: str
    source_file: str
    description: str | None = None
    business_key: Column | None = None
    columns: list[Column] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    seed_rows: int | None = None
    seed_values: list[list[Any]] = field(default_factory=list)
    scd: str = "type1"
    row_count: int | None = None
    explicit_key: bool = False
    columnstore: bool = False
    fact_type: str | None = None
    grain: str | None = None
    primary_key: list[str] = field(default_factory=list)
    unique_members: bool = True
    is_hierarchy: bool = False
    hierarchy_name: str | None = None
    hierarchy_ordinal: int | None = None

    @property
    def key_column(self) -> str:
        if self.explicit_key and self.business_key:
            return self.business_key.name
        return f"{self.name}Key"


@dataclass
class Model:
    tables: list[Table]

    def by_name(self) -> dict[str, Table]:
        return {t.name: t for t in self.tables}

    def by_schema_name(self) -> dict[tuple[str, str], Table]:
        return {(t.schema, t.name): t for t in self.tables}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _col(raw: dict[str, Any], default_nullable: bool = True) -> Column:
    return Column(
        name=str(raw["name"]),
        type=str(raw["type"]),
        nullable=bool(raw.get("nullable", default_nullable)),
        default=None if raw.get("default") is None else str(raw.get("default")),
        identity=bool(raw.get("identity", False)),
        aggregation=None if raw.get("aggregation") is None else str(raw.get("aggregation")),
    )


def _business_key(raw: dict[str, Any] | None) -> Column | None:
    if not raw:
        return None
    return Column(name=str(raw["name"]), type=str(raw["type"]), nullable=False)


def _fk_column_name(parent: str, role: str | None) -> str:
    if parent == "SourceSystem" and role is None:
        return "SourceSystemRefKey"
    return f"{role or ''}{parent}Key"


def _natural_key_name(parent: Table, role: str | None) -> str:
    if not parent.business_key:
        return f"{role or ''}{parent.name}Key"
    if parent.business_key.name == f"{parent.name}Key":
        return f"{role or ''}{parent.business_key.name}"
    return f"{role or ''}{parent.name}{parent.business_key.name}"


def _fk(raw: dict[str, Any], parent_schema: str, source_kind: str) -> ForeignKey:
    parent = str(raw["name"])
    role = None if raw.get("role") is None else str(raw.get("role"))
    return ForeignKey(
        parent_table=parent,
        parent_schema=parent_schema,
        column_name=_fk_column_name(parent, role),
        role=role,
        nullable=bool(raw.get("nullable", False)),
        source_kind=source_kind,
    )


def _remove_named(cols: list[Column], name: str) -> tuple[list[Column], Column | None]:
    kept: list[Column] = []
    found: Column | None = None
    for col in cols:
        if col.name == name and found is None:
            found = col
        else:
            kept.append(col)
    return kept, found


def _subject_area(path: Path, data: dict[str, Any], root: Path) -> str:
    if data.get("subject_area"):
        return str(data["subject_area"])
    if path.parent.name == "common":
        return path.stem
    try:
        return path.relative_to(root).with_suffix("").as_posix().replace("/", "_")
    except ValueError:
        return path.stem


def _load_yaml_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML value must be a mapping")
    return data


def load_model(model_root: str | Path = "model") -> Model:
    root = Path(model_root)
    files = sorted([*root.glob("common/*.yaml"), *root.glob("subject_areas/*.yaml")], key=lambda p: str(p).lower())
    tables: list[Table] = []

    for path in files:
        data = _load_yaml_file(path)
        sa = _subject_area(path, data, root)
        source = str(path)

        for raw in _as_list(data.get("reference")):
            cols = [_col(c) for c in _as_list(raw.get("columns"))]
            bk = _business_key(raw.get("business_key"))
            tables.append(Table(
                name=str(raw["name"]),
                schema="ref",
                kind="ref",
                subject_area=sa,
                source_file=source,
                description=raw.get("description"),
                business_key=bk,
                columns=cols,
                foreign_keys=[_fk(p, "ref", "parents") for p in _as_list(raw.get("parents"))],
                seed_rows=raw.get("seed_rows"),
                seed_values=[list(v) for v in _as_list(raw.get("seed_values"))],
            ))

        for raw in _as_list(data.get("hierarchies")):
            prev: str | None = None
            schema = str(raw.get("schema", "dim"))
            for ordinal, level in enumerate(_as_list(raw.get("levels"))):
                name = str(level["name"])
                cols = [_col(c) for c in _as_list(level.get("columns"))]
                code_name = f"{name}Code"
                name_name = f"{name}Name"
                cols, supplied_code = _remove_named(cols, code_name)
                bk = Column(code_name, "nvarchar(30)", nullable=False) if supplied_code is None else Column(supplied_code.name, supplied_code.type, nullable=False, default=supplied_code.default, identity=supplied_code.identity)
                if not any(c.name == name_name for c in cols):
                    cols.insert(0, Column(name_name, "nvarchar(120)", nullable=False))
                fks = [_fk({"name": prev}, schema, "parents")] if prev else []
                tables.append(Table(
                    name=name,
                    schema=schema,
                    kind="dim" if schema == "dim" else schema,
                    subject_area=sa,
                    source_file=source,
                    description=level.get("description", raw.get("description")),
                    business_key=bk,
                    columns=cols,
                    foreign_keys=fks,
                    scd=str(level.get("scd", "type1")),
                    row_count=level.get("row_count"),
                    is_hierarchy=True,
                    hierarchy_name=str(raw.get("name", "")),
                    hierarchy_ordinal=ordinal,
                ))
                prev = name

        for raw in _as_list(data.get("dimensions")):
            tables.append(Table(
                name=str(raw["name"]),
                schema="dim",
                kind="dim",
                subject_area=sa,
                source_file=source,
                description=raw.get("description"),
                business_key=_business_key(raw.get("business_key")),
                columns=[_col(c) for c in _as_list(raw.get("columns"))],
                foreign_keys=[_fk(p, "dim", "parents") for p in _as_list(raw.get("parents"))]
                + [_fk(p, "ref", "ref_parents") for p in _as_list(raw.get("ref_parents"))],
                scd=str(raw.get("scd", "type1")),
                row_count=raw.get("row_count"),
                explicit_key=bool(raw.get("explicit_key", False)),
            ))

        for raw in _as_list(data.get("bridges")):
            tables.append(Table(
                name=str(raw["name"]),
                schema="bridge",
                kind="bridge",
                subject_area=sa,
                source_file=source,
                description=raw.get("description"),
                columns=[_col(c) for c in _as_list(raw.get("columns"))],
                foreign_keys=[_fk(p, "dim", "members") for p in _as_list(raw.get("members"))]
                + [_fk(p, "ref", "ref_parents") for p in _as_list(raw.get("ref_parents"))],
                row_count=raw.get("row_count"),
                unique_members=bool(raw.get("unique_members", True)),
            ))

        for raw in _as_list(data.get("facts")):
            fks: list[ForeignKey] = []
            fks.extend(ForeignKey("Date", "dim", f"{role}DateKey", str(role), False, "date_roles") for role in _as_list(raw.get("date_roles")))
            fks.extend(ForeignKey("TimeOfDay", "dim", f"{role}TimeOfDayKey", str(role), False, "time_roles") for role in _as_list(raw.get("time_roles")))
            fks.extend(_fk(p, "dim", "dimensions") for p in _as_list(raw.get("dimensions")))
            fks.extend(_fk(p, "ref", "ref_parents") for p in _as_list(raw.get("ref_parents")))
            tables.append(Table(
                name=str(raw["name"]),
                schema="fact",
                kind="fact",
                subject_area=sa,
                source_file=source,
                description=raw.get("description"),
                columns=[_col(c, default_nullable=False) for c in _as_list(raw.get("degenerate"))]
                + [_col(c, default_nullable=False) for c in _as_list(raw.get("measures"))],
                foreign_keys=fks,
                row_count=raw.get("row_count"),
                columnstore=bool(raw.get("columnstore", False)),
                fact_type=str(raw.get("type", "transaction")),
                grain=raw.get("grain"),
            ))

        for raw in _as_list(data.get("etl_tables")):
            tables.append(Table(
                name=str(raw["name"]),
                schema="etl",
                kind="etl",
                subject_area=sa,
                source_file=source,
                description=raw.get("description"),
                columns=[_col(c) for c in _as_list(raw.get("columns"))],
                primary_key=[str(c) for c in _as_list(raw.get("primary_key"))],
            ))

    _add_conformed_placeholders(tables)
    return Model(sorted(tables, key=lambda t: (t.subject_area, t.schema, t.name)))


def _add_conformed_placeholders(tables: list[Table]) -> None:
    existing = {t.name for t in tables}
    source = "model/CONFORMED.md"

    refs = {
        "SourceSystem": "SourceSystemCode",
        "RecordStatus": "RecordStatusCode",
        "UnitOfMeasure": "UnitOfMeasureCode",
        "UnitOfMeasureClass": "UnitOfMeasureClassCode",
        "Currency": "CurrencyCode",
        "CurrencyRegion": "CurrencyRegionCode",
        "Country": "CountryCode",
        "Region": "RegionCode",
        "Continent": "ContinentCode",
        "Language": "LanguageCode",
        "TimeZone": "TimeZoneCode",
        "Severity": "SeverityCode",
        "Priority": "PriorityCode",
        "ApprovalStatus": "ApprovalStatusCode",
        "TransactionType": "TransactionTypeCode",
        "AdjustmentReason": "AdjustmentReasonCode",
        "YesNoFlag": "YesNoFlagCode",
        "MeasurementSystem": "MeasurementSystemCode",
        "HazardClass": "HazardClassCode",
        "ComplianceStandard": "ComplianceStandardCode",
    }
    for name, bk in sorted(refs.items()):
        if name not in existing:
            tables.append(Table(
                name=name,
                schema="ref",
                kind="ref",
                subject_area="conformed",
                source_file=source,
                business_key=Column(bk, "nvarchar(30)", nullable=False),
                columns=[Column("SourceSystemName" if name == "SourceSystem" else f"{name}Name", "nvarchar(120)", nullable=False)],
            ))
            existing.add(name)

    hierarchies = {
        "ProductTaxonomy": ["ProductDivision", "ProductLine", "ProductFamily", "ProductSubfamily"],
        "GeographyTaxonomy": ["GeoContinent", "GeoCountry", "GeoStateProvince", "GeoCity", "GeoPostalCode"],
        "OrganizationTaxonomy": ["OrgEnterprise", "OrgDivision", "OrgRegion", "OrgBusinessUnit"],
        "CalendarTaxonomy": ["CalendarYear", "CalendarQuarter", "CalendarMonth", "CalendarWeek"],
        "FiscalTaxonomy": ["FiscalYear", "FiscalQuarter", "FiscalPeriod"],
        "AccountTaxonomy": ["AccountCategory", "AccountGroup", "AccountSubGroup"],
    }
    for hierarchy, levels in sorted(hierarchies.items()):
        prev: str | None = None
        for ordinal, name in enumerate(levels):
            if name not in existing:
                tables.append(Table(
                    name=name,
                    schema="dim",
                    kind="dim",
                    subject_area="conformed",
                    source_file=source,
                    business_key=Column(f"{name}Code", "nvarchar(30)", nullable=False),
                    columns=[Column(f"{name}Name", "nvarchar(120)", nullable=False)],
                    foreign_keys=[ForeignKey(prev, "dim", f"{prev}Key", None, False, "parents")] if prev else [],
                    is_hierarchy=True,
                    hierarchy_name=hierarchy,
                    hierarchy_ordinal=ordinal,
                ))
                existing.add(name)
            prev = name

    dims: dict[str, tuple[str, str, str | None, list[str]]] = {
        "Date": ("DateKey", "int", None, []),
        "TimeOfDay": ("TimeOfDayKey", "int", None, []),
        "Product": ("ProductNumber", "nvarchar(40)", "type2", ["ProductSubfamily"]),
        "Plant": ("PlantCode", "nvarchar(30)", "type2", ["GeoPostalCode", "OrgBusinessUnit"]),
        "Site": ("SiteCode", "nvarchar(30)", "type1", ["Plant"]),
        "WorkCenter": ("WorkCenterCode", "nvarchar(30)", "type2", ["Plant"]),
        "Employee": ("EmployeeNumber", "nvarchar(30)", "type2", ["OrgBusinessUnit"]),
        "Customer": ("CustomerNumber", "nvarchar(30)", "type2", ["GeoPostalCode"]),
        "Supplier": ("SupplierNumber", "nvarchar(30)", "type2", ["GeoPostalCode"]),
        "Asset": ("AssetNumber", "nvarchar(30)", "type2", ["Plant"]),
        "GlAccount": ("GlAccountNumber", "nvarchar(30)", "type1", ["AccountSubGroup"]),
        "CostCenter": ("CostCenterCode", "nvarchar(30)", "type1", ["OrgBusinessUnit"]),
        "BusinessUnit": ("BusinessUnitCode", "nvarchar(30)", "type1", ["OrgBusinessUnit"]),
    }
    for name, (bk, typ, scd, parents) in sorted(dims.items()):
        if name not in existing:
            explicit = name in {"Date", "TimeOfDay"}
            tables.append(Table(
                name=name,
                schema="dim",
                kind="dim",
                subject_area="conformed",
                source_file=source,
                business_key=Column(bk, typ, nullable=False),
                columns=[Column(f"{name}Name", "nvarchar(120)", nullable=False)] if not explicit else [],
                foreign_keys=[ForeignKey(parent, "dim", f"{parent}Key", None, False, "parents") for parent in parents],
                scd=scd or "type1",
                explicit_key=explicit,
            ))
            existing.add(name)


def staging_columns(table: Table, model: Model) -> list[Column]:
    by_schema_name = model.by_schema_name()
    cols: list[Column] = []
    if table.business_key:
        cols.append(table.business_key)
    cols.extend(table.columns)
    for fk in table.foreign_keys:
        parent = by_schema_name.get((fk.parent_schema, fk.parent_table))
        if parent and parent.business_key:
            cols.append(Column(_natural_key_name(parent, fk.role), parent.business_key.type, nullable=fk.nullable))
    cols.append(Column("BatchId", "bigint", nullable=False))
    cols.append(Column("RowNumber", "bigint", nullable=False))
    return cols
