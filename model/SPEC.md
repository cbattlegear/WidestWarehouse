# WidestWarehouse Model Specification (v1)

This document is the **contract** between the YAML metadata model (`model/`) and the
DDL generator (`tools/generator/`). Both sides must conform to it exactly.

`sql/` is **generated output**. Never hand-edit it.

---

## 1. File layout

```
model/
  common/
    reference.yaml      # ref-schema lookup tables (conformed, cross-area)
    date.yaml           # dim.Date, dim.TimeOfDay and their outriggers
    etl.yaml            # etl-schema control tables (hand-specified columns)
  subject_areas/
    <area>.yaml         # one file per subject area
```

Every YAML file is a mapping with the top-level keys described in §3. All keys are
optional; a missing key is treated as an empty list.

---

## 2. Global conventions the generator enforces

| Rule | Detail |
|---|---|
| Naming | `PascalCase`, **singular** table names. No spaces, no underscores in table names. |
| Surrogate key | Every `ref`, `dim` and `bridge` table gets `<TableName>Key INT IDENTITY(1,1) NOT NULL` as clustered PK. |
| Business key | Declared via `business_key`; generator adds a `UNIQUE` nonclustered index. |
| Unknown member | Every `ref` and `dim` table gets a `-1` row via `IDENTITY_INSERT` in the seed step. |
| FK column name | Parent `Product` referenced from a child produces column `ProductKey`. With `role: Ship`, produces `ShipProductKey`. |
| FK constraint name | `FK_<childschema>_<childtable>_<fkcolumn>` |
| Index name | `IX_<table>_<col1><col2>` / `UX_<table>_<cols>` for unique |
| Audit columns | Every `ref`/`dim`/`bridge` table gets `SourceSystemKey INT NOT NULL DEFAULT -1`, `BatchId BIGINT NOT NULL DEFAULT 0`, `LoadedAtUtc DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME()`. |
| SCD2 columns | When `scd: type2`: `EffectiveFromUtc DATETIME2(3) NOT NULL`, `EffectiveToUtc DATETIME2(3) NOT NULL DEFAULT '9999-12-31'`, `IsCurrent BIT NOT NULL DEFAULT 1`, `RowHash BINARY(32) NOT NULL`. |
| FKs | Emitted in a **separate pass** (`sql/50_constraints/`), never inline. Table creation order is therefore irrelevant. |
| Collation | Database created with `SQL_Latin1_General_CP1_CI_AS`. |

### Allowed column types

`int`, `bigint`, `smallint`, `tinyint`, `bit`, `decimal(p,s)`, `money`,
`float`, `date`, `time(0..7)`, `datetime2(0..7)`, `nvarchar(n)`, `nvarchar(max)`,
`char(n)`, `varbinary(n)`, `uniqueidentifier`.

Types are written lowercase in YAML and emitted uppercase in SQL.

---

## 3. Top-level YAML keys

### 3.1 `subject_area` (string, required in `subject_areas/*.yaml`)

Lowercase snake_case identifier, e.g. `production_execution`. Used only for grouping
and for the header comment in emitted files.

### 3.2 `reference` — list of `ref`-schema lookup tables

```yaml
reference:
  - name: WorkOrderStatus
    description: Lifecycle states of a production work order.
    business_key: {name: StatusCode, type: "nvarchar(20)"}
    seed_rows: 12                     # how many rows the seeder should create
    seed_values:                      # optional: exact values instead of synthetic
      - [RELEASED, "Released", 0]
      - [CLOSED,   "Closed",   1]
    columns:
      - {name: StatusName, type: "nvarchar(60)", nullable: false}
      - {name: IsTerminal, type: bit, nullable: false, default: "0"}
    parents:                          # FKs to other ref tables only
      - {name: WorkOrderStatusGroup}
```

`seed_values` rows must match `[business_key] + columns` order.

### 3.3 `hierarchies` — snowflake outrigger chains (this is what creates depth)

```yaml
hierarchies:
  - name: ProductTaxonomy
    schema: dim
    description: Product classification from division down to subfamily.
    levels:
      - name: ProductDivision
        row_count: 8
        columns:
          - {name: DivisionName, type: "nvarchar(80)", nullable: false}
      - name: ProductLine
        row_count: 40
      - name: ProductFamily
        row_count: 180
      - name: ProductSubfamily
        row_count: 600
```

Expansion rules:
- Each level becomes **its own table** in the given `schema` (default `dim`).
- Level *i* gets an FK to level *i-1*. Level 0 has no parent.
- Each level automatically gets a business key `<LevelName>Code nvarchar(30)` and a
  name column `<LevelName>Name nvarchar(120)` unless `columns` supplies them.
- Levels are Type 1. To make a level Type 2, set `scd: type2` on that level.
- **Leaf dimensions attach to the last level** by listing it in their `parents`.

A 4-level hierarchy therefore yields 4 tables.

### 3.4 `dimensions` — leaf dimensions in `dim`

```yaml
dimensions:
  - name: Product
    description: Manufacturable or purchasable item master.
    scd: type2                        # type1 (default) | type2
    row_count: 25000
    business_key: {name: ProductNumber, type: "nvarchar(40)"}
    columns:
      - {name: ProductName,   type: "nvarchar(200)", nullable: false}
      - {name: NetWeightKg,   type: "decimal(18,4)", nullable: true}
      - {name: IsPhantom,     type: bit, nullable: false, default: "0"}
    parents:                          # FKs to dim tables / hierarchy levels
      - {name: ProductSubfamily}
      - {name: ProductBrand}
      - {name: Product, role: Parent, nullable: true}   # self-reference is allowed
    ref_parents:                      # FKs to ref tables
      - {name: UnitOfMeasure}
      - {name: ProductStatus}
```

`parents` / `ref_parents` entry fields: `name` (required), `role` (optional prefix),
`nullable` (default `false`).

### 3.5 `bridges` — many-to-many resolvers in `bridge`

```yaml
bridges:
  - name: BillOfMaterialComponent
    description: Resolves parent product to component products.
    row_count: 400000
    members:
      - {name: Product, role: Assembly}
      - {name: Product, role: Component}
    ref_parents:
      - {name: UnitOfMeasure}
    columns:
      - {name: QuantityPer, type: "decimal(18,6)", nullable: false}
      - {name: ScrapFactor, type: "decimal(9,6)", nullable: false, default: "0"}
```

Bridges get a surrogate key like dimensions plus a unique index over all member key
columns (override with `unique_members: false`).

### 3.6 `facts` — fact tables in `fact`

```yaml
facts:
  - name: ProductionOrderOperation
    description: One row per work order operation confirmation.
    type: transaction                 # transaction | periodic_snapshot | accumulating_snapshot
    grain: "One row per work order, per operation, per confirmation event."
    row_count: 2500000
    columnstore: true                 # clustered columnstore instead of rowstore
    date_roles: [Order, Start, Finish]   # -> OrderDateKey, StartDateKey, FinishDateKey
    time_roles: [Start, Finish]          # -> StartTimeOfDayKey, FinishTimeOfDayKey
    dimensions:
      - {name: Product}
      - {name: Plant}
      - {name: WorkCenter}
      - {name: Employee, role: Operator, nullable: true}
    ref_parents:
      - {name: WorkOrderStatus}
    degenerate:                       # degenerate dimensions, stored inline
      - {name: WorkOrderNumber, type: "nvarchar(30)"}
      - {name: OperationSequence, type: int}
    measures:
      - {name: GoodQuantity,   type: "decimal(18,4)", aggregation: sum}
      - {name: ScrapQuantity,  type: "decimal(18,4)", aggregation: sum}
      - {name: RunMinutes,     type: "decimal(18,4)", aggregation: sum, nullable: true}
```

Fact rules the generator applies:
- PK is `<FactName>Key BIGINT IDENTITY(1,1)`, clustered **unless** `columnstore: true`,
  in which case the PK is nonclustered and a clustered columnstore index is created.
- All `date_roles` / `time_roles` / `dimensions` / `ref_parents` become `NOT NULL INT`
  FK columns (`nullable: true` allows NULL) defaulting to `-1`.
- Every fact gets `SourceSystemKey`, `BatchId`, `LoadedAtUtc`.
- `accumulating_snapshot` facts additionally get a `<Fact>LagDays int NULL` column set.
- A nonclustered index is created on every FK column.

### 3.7 `etl_tables` — used only by `model/common/etl.yaml`

Free-form tables in the `etl` schema; no surrogate/audit column injection.

```yaml
etl_tables:
  - name: BatchRun
    primary_key: [BatchId]
    columns:
      - {name: BatchId, type: bigint, identity: true, nullable: false}
      - {name: Status,  type: "nvarchar(20)", nullable: false}
```

---

## 4. Staging derivation (automatic — do not author)

For every `dim` with `scd: type2`, every `bridge`, and every `fact`, the generator emits a
matching `stg.<TableName>` table containing the same business/attribute/measure columns
but **no** surrogate key, **no** FK key columns (natural keys instead), and no constraints.
Natural-key columns are named `<Parent><BusinessKeyName>`, e.g. `ProductProductNumber`.

Each `stg` table gets `BatchId BIGINT NOT NULL` and `RowNumber BIGINT NOT NULL`.

---

## 5. Emitted SQL layout

| Folder | Contents |
|---|---|
| `sql/00_database/` | database creation, schemas, `etl.SourceSystem` bootstrap |
| `sql/10_reference/` | all `ref` tables |
| `sql/20_dimensions/` | all `dim` tables (hierarchy levels first, then leaves) |
| `sql/30_bridges/` | all `bridge` tables |
| `sql/40_facts/` | all `fact` tables |
| `sql/50_constraints/` | every FK, in dependency order |
| `sql/60_indexes/` | unique business-key indexes, FK indexes, columnstore |
| `sql/70_views/` | one flattened `dim.vwDim<Name>` per leaf dimension |
| `sql/80_etl_control/` | `etl` tables + seed of `etl.SourceSystem` |
| `sql/90_staging/` | derived `stg` tables |
| `sql/build_all.sql` | `:r` includes in the exact order above (sqlcmd mode) |

One file per subject area within each folder, named `<NN>_<subject_area>.sql`.

---

## 6. Validation the generator must perform (fail loudly)

1. Duplicate table name across the whole model.
2. FK target that does not exist, or targets the wrong schema class
   (`ref_parents` must resolve to `ref`, `parents`/`dimensions` to `dim`).
3. Cycles in the FK graph, **except** explicit self-references.
4. Reserved T-SQL words used as table or column names.
5. Identifier longer than 116 chars (leaves room for constraint-name prefixes).
6. Unknown column type not in the allowed list.
7. Duplicate column name within a table after key/audit injection.
8. `seed_values` row width mismatching the declared columns.

Validation errors are collected and reported together, not one at a time.

---

## 7. Table budget target

The full model must produce **at least 700** base tables. `scripts/count_tables.sql`
asserts this. Approximate split: `ref` 95, `dim` 355, `bridge` 48, `fact` 68,
`stg` 120, `etl` 26.
