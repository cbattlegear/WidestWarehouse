from __future__ import annotations

import csv
import datetime as dt
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.generator.ddl_emitter import table_columns
from tools.generator.graph import topological_sort
from tools.generator.model_loader import Model, Table, load_model

from .rng import table_rng
from .value_factories import type_info, unknown_value, value_for

DATE_START = dt.date(2018, 1, 1)
DATE_END = dt.date(2030, 12, 31)
TIME_GRAIN_SECONDS = 60
DIM_CAP = 500
BRIDGE_CAP = 500
FACT_TARGET_ROWS = 500_000
DEFAULT_REF_ROWS = 8

@dataclass
class SeedResult:
    rows_by_table: dict[tuple[str, str], int] = field(default_factory=dict)
    rows_by_schema: dict[str, int] = field(default_factory=dict)
    key_sets: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    csv_paths: dict[tuple[str, str], Path] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_table.values())


def q(name: str) -> str:
    return "[" + name.replace("]", "]]" ) + "]"


def _clean_dir(path: Path, suffix: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.rglob(f"*{suffix}"):
        child.unlink()


def _csv_path(out: Path, table: Table) -> Path:
    return out / table.schema / f"{table.name}.csv"


def _row_count(table: Table, scale: float, model: Model, fact_rows: dict[str, int]) -> int:
    if table.kind == "ref":
        return len(table.seed_values) if table.seed_values else max(1, int(round((table.seed_rows or DEFAULT_REF_ROWS) * scale)))
    if table.kind == "dim":
        if table.name == "Date":
            return (DATE_END - DATE_START).days + 1
        if table.name == "TimeOfDay":
            return 86400 // TIME_GRAIN_SECONDS
        return min(max(1, int(round((table.row_count or 10) * scale))), max(1, int(round(DIM_CAP * scale))))
    if table.kind == "bridge":
        return min(max(1, int(round((table.row_count or 100) * scale))), max(1, int(round(BRIDGE_CAP * scale))))
    if table.kind == "fact":
        return fact_rows.get(table.name, 0)
    return 0


def _fact_distribution(model: Model, scale: float) -> dict[str, int]:
    facts = [t for t in model.tables if t.kind == "fact"]
    total_weight = sum(t.row_count or 1 for t in facts) or 1
    target = max(len(facts), int(FACT_TARGET_ROWS * scale))
    rows: dict[str, int] = {}
    allocated = 0
    for table in sorted(facts, key=lambda t: t.name):
        n = max(1, int(round(target * ((table.row_count or 1) / total_weight))))
        rows[table.name] = n
        allocated += n
    # correct rounding deterministically
    diff = target - allocated
    names = sorted(rows)
    i = 0
    while diff != 0 and names:
        name = names[i % len(names)]
        if diff > 0:
            rows[name] += 1
            diff -= 1
        elif rows[name] > 1:
            rows[name] -= 1
            diff += 1
        i += 1
    return rows


def _parent_keys(result: SeedResult, fk) -> list[int]:
    keys = sorted(result.key_sets.get((fk.parent_schema, fk.parent_table), {-1}))
    positive = [k for k in keys if k != -1]
    return positive or keys


def _choose_fk(result: SeedResult, fk, seq: int, rng) -> int:
    keys = _parent_keys(result, fk)
    if not keys:
        return -1
    # spread children across parent domains stably; allow occasional unknowns for nullable refs.
    if fk.nullable and seq % 17 == 0:
        return -1
    return keys[(seq + rng.randrange(len(keys))) % len(keys)]


def _unknown_row(table: Table, cols) -> list[Any]:
    return [unknown_value(c) for c in cols]


def _date_rows(table: Table, cols, result: SeedResult, rng) -> list[list[Any]]:
    rows = [_unknown_row_stub(cols, {"DateKey": -1})]
    today = dt.date.today()
    cur = DATE_START
    cal_keys = sorted(k for k in result.key_sets.get(("dim", "CalendarWeek"), {-1}) if k != -1) or [-1]
    fiscal_keys = sorted(k for k in result.key_sets.get(("dim", "FiscalPeriod"), {-1}) if k != -1) or [-1]
    yesno_keys = sorted(k for k in result.key_sets.get(("ref", "YesNoFlag"), {-1}) if k != -1) or [-1]
    while cur <= DATE_END:
        iso = cur.isocalendar()
        quarter = ((cur.month - 1) // 3) + 1
        fiscal_month = ((cur.month - 7) % 12) + 1
        fiscal_year = cur.year + (1 if cur.month >= 7 else 0)
        fiscal_quarter = ((fiscal_month - 1) // 3) + 1
        next_day = cur + dt.timedelta(days=1)
        vals = {
            "DateKey": int(cur.strftime("%Y%m%d")),
            "CalendarWeekKey": cal_keys[(iso.week - 1) % len(cal_keys)],
            "FiscalPeriodKey": fiscal_keys[(fiscal_month - 1) % len(fiscal_keys)],
            "YesNoFlagKey": yesno_keys[1 % len(yesno_keys)] if cur.weekday() >= 5 and len(yesno_keys) > 1 else yesno_keys[0],
            "FullDate": cur.isoformat(),
            "DayName": cur.strftime("%A"),
            "DayOfWeekNumber": (cur.weekday() + 1) % 7 + 1,  # Sunday=1
            "IsoDayOfWeekNumber": iso.weekday,
            "DayOfMonthNumber": cur.day,
            "DayOfYearNumber": cur.timetuple().tm_yday,
            "WeekOfYearNumber": int(cur.strftime("%U")) + 1,
            "IsoWeekNumber": iso.week,
            "CalendarMonthNumber": cur.month,
            "CalendarMonthName": cur.strftime("%B"),
            "CalendarMonthShortName": cur.strftime("%b"),
            "CalendarQuarterNumber": quarter,
            "CalendarYearNumber": cur.year,
            "FiscalPeriodNumber": fiscal_month,
            "FiscalQuarterNumber": fiscal_quarter,
            "FiscalYearNumber": fiscal_year,
            "IsWeekend": 1 if cur.weekday() >= 5 else 0,
            "IsHoliday": 1 if (cur.month, cur.day) in {(1, 1), (7, 4), (12, 25)} else 0,
            "HolidayName": { (1, 1): "New Year's Day", (7, 4): "Independence Day", (12, 25): "Christmas Day"}.get((cur.month, cur.day), "N/A"),
            "JulianDayNumber": cur.toordinal(),
            "DaysFromToday": (cur - today).days,
            "WeeksFromToday": (cur - today).days // 7,
            "MonthsFromToday": (cur.year - today.year) * 12 + cur.month - today.month,
            "IsMonthEnd": 1 if next_day.month != cur.month else 0,
            "IsQuarterEnd": 1 if next_day.month != cur.month and cur.month in {3, 6, 9, 12} else 0,
            "IsYearEnd": 1 if cur.month == 12 and cur.day == 31 else 0,
            "SourceSystemKey": _source_system_key(result),
            "BatchId": 1,
            "LoadedAtUtc": "2026-08-24T00:00:00.000",
        }
        rows.append([vals.get(c.name, unknown_value(c)) for c in cols])
        cur += dt.timedelta(days=1)
    return rows


def _time_rows(table: Table, cols, result: SeedResult, rng) -> list[list[Any]]:
    rows = [_unknown_row_stub(cols, {"TimeOfDayKey": -1})]
    shift_keys = sorted(k for k in result.key_sets.get(("ref", "ShiftCode"), {-1}) if k != -1) or [-1]
    for second in range(0, 86400, TIME_GRAIN_SECONDS):
        hour = second // 3600
        minute = (second // 60) % 60
        sec = second % 60
        shift = "Night" if hour < 6 or hour >= 22 else ("Day" if hour < 14 else "Evening")
        vals = {
            "TimeOfDayKey": second,
            "ShiftCodeKey": shift_keys[(hour // 8) % len(shift_keys)],
            "TimeValue": f"{hour:02d}:{minute:02d}:{sec:02d}",
            "HourNumber": hour,
            "MinuteNumber": minute,
            "SecondNumber": sec,
            "MinuteOfDayNumber": second // 60,
            "SecondOfDayNumber": second,
            "AmPmCode": "AM" if hour < 12 else "PM",
            "HourBucketName": f"{hour:02d}:00-{hour:02d}:59",
            "ShiftBucketName": shift,
            "IsBusinessHour": 1 if 8 <= hour < 17 else 0,
            "DisplayTime": dt.time(hour, minute, sec).strftime("%I:%M %p"),
            "SourceSystemKey": _source_system_key(result),
            "BatchId": 1,
            "LoadedAtUtc": "2026-08-24T00:00:00.000",
        }
        rows.append([vals.get(c.name, unknown_value(c)) for c in cols])
    return rows


def _unknown_row_stub(cols, overrides: dict[str, Any]) -> list[Any]:
    vals = []
    for c in cols:
        vals.append(overrides.get(c.name, unknown_value(c)))
    return vals


def _normal_row(table: Table, cols, seq: int, result: SeedResult, rng) -> list[Any]:
    values: dict[str, Any] = {}
    if table.kind in {"ref", "dim", "bridge"} and not table.explicit_key:
        values[table.key_column] = seq
    elif table.kind == "fact":
        values[table.key_column] = seq
    elif table.explicit_key and table.name not in {"Date", "TimeOfDay"}:
        values[table.key_column] = seq

    for fk in table.foreign_keys:
        values[fk.column_name] = _choose_fk(result, fk, seq, rng)

    for c in cols:
        if c.name in values:
            continue
        if c.name == "SourceSystemKey":
            values[c.name] = _source_system_key(result)
        elif c.name == "BatchId":
            values[c.name] = 1
        elif c.name == "LoadedAtUtc":
            values[c.name] = "2026-08-24T00:00:00.000"
        elif c.name == "EffectiveFromUtc":
            values[c.name] = "2018-01-01T00:00:00.000"
        elif c.name == "EffectiveToUtc":
            values[c.name] = "9999-12-31T00:00:00.000"
        elif c.name == "IsCurrent":
            values[c.name] = 1
        else:
            values[c.name] = value_for(c, table.name, seq, rng)
    _make_fact_consistent(table, values)
    return [values[c.name] for c in cols]


def _source_system_key(result: SeedResult) -> int:
    keys = sorted(k for k in result.key_sets.get(("ref", "SourceSystem"), {-1}) if k != -1)
    return keys[0] if keys else -1


def _make_fact_consistent(table: Table, values: dict[str, Any]) -> None:
    names = set(values)
    if {"GoodQuantity", "ScrapQuantity", "TotalQuantity"}.issubset(names):
        total = 100 + (int(values.get(table.key_column, 1)) % 900)
        scrap = total // 25
        good = total - scrap
        values["TotalQuantity"] = f"{total:.4f}"
        values["ScrapQuantity"] = f"{scrap:.4f}"
        values["GoodQuantity"] = f"{good:.4f}"
    for name in list(names):
        if (name.endswith("Minutes") or name.endswith("Hours")) and str(values[name]).startswith("-"):
            values[name] = str(values[name]).lstrip("-")


def _seed_value_rows(table: Table, cols, result: SeedResult, rng) -> list[list[Any]]:
    rows: list[list[Any]] = []
    prefix_cols = []
    if table.business_key:
        prefix_cols.append(table.business_key)
    prefix_cols.extend(table.columns)
    for seq, seed in enumerate(table.seed_values, start=1):
        vals: dict[str, Any] = {table.key_column: seq}
        for fk in table.foreign_keys:
            vals[fk.column_name] = _choose_fk(result, fk, seq, rng)
        for c, raw in zip(prefix_cols, seed, strict=True):
            vals[c.name] = raw
        for c in cols:
            if c.name not in vals:
                if c.name == "SourceSystemKey":
                    vals[c.name] = _source_system_key(result)
                elif c.name == "BatchId":
                    vals[c.name] = 1
                elif c.name == "LoadedAtUtc":
                    vals[c.name] = "2026-08-24T00:00:00.000"
                else:
                    vals[c.name] = value_for(c, table.name, seq, rng)
        rows.append([vals[c.name] for c in cols])
    return rows


def _bridge_rows(table: Table, cols, count: int, result: SeedResult, rng) -> list[list[Any]]:
    rows: list[list[Any]] = []
    seen: set[tuple[int, ...]] = set()
    member_cols = [fk.column_name for fk in table.foreign_keys if fk.source_kind == "members"]
    seq = 1
    attempts = 0
    while len(rows) < count and attempts < count * 25 + 100:
        attempts += 1
        vals: dict[str, Any] = {table.key_column: seq}
        for fk in table.foreign_keys:
            vals[fk.column_name] = _choose_fk(result, fk, seq + attempts, rng)
        combo = tuple(vals[c] for c in member_cols)
        if table.unique_members and combo in seen:
            continue
        seen.add(combo)
        for c in cols:
            if c.name not in vals:
                if c.name == "SourceSystemKey":
                    vals[c.name] = _source_system_key(result)
                elif c.name == "BatchId":
                    vals[c.name] = 1
                elif c.name == "LoadedAtUtc":
                    vals[c.name] = "2026-08-24T00:00:00.000"
                else:
                    vals[c.name] = value_for(c, table.name, seq, rng)
        rows.append([vals[c.name] for c in cols])
        seq += 1
    return rows


def _fact_row(table: Table, cols, seq: int, result: SeedResult, rng, date_window: list[int]) -> list[Any]:
    vals: dict[str, Any] = {table.key_column: seq}
    time_keys = sorted(k for k in result.key_sets.get(("dim", "TimeOfDay"), {-1}) if k != -1)
    for fk in table.foreign_keys:
        if fk.parent_table == "Date":
            vals[fk.column_name] = date_window[(seq + rng.randrange(len(date_window))) % len(date_window)]
        elif fk.parent_table == "TimeOfDay":
            vals[fk.column_name] = time_keys[(seq + rng.randrange(len(time_keys))) % len(time_keys)] if time_keys else -1
        else:
            vals[fk.column_name] = _choose_fk(result, fk, seq, rng)
    for c in cols:
        if c.name in vals:
            continue
        if c.name == "SourceSystemKey":
            vals[c.name] = _source_system_key(result)
        elif c.name == "BatchId":
            vals[c.name] = 1
        elif c.name == "LoadedAtUtc":
            vals[c.name] = "2026-08-24T00:00:00.000"
        else:
            vals[c.name] = value_for(c, table.name, seq, rng)
    _make_fact_consistent(table, vals)
    return [vals[c.name] for c in cols]


def _etl_rows(table: Table, model: Model) -> list[list[Any]]:
    cols = table_columns(table)
    rows: list[list[Any]] = []
    systems = [(1, "ERP", "Enterprise Resource Planning", "erp-primary", 1, "2026-08-24T00:00:00.000"), (2, "MES", "Manufacturing Execution", "mes-primary", 1, "2026-08-24T00:00:00.000"), (3, "WMS", "Warehouse Management", "wms-primary", 1, "2026-08-24T00:00:00.000"), (4, "CMMS", "Maintenance Management", "cmms-primary", 1, "2026-08-24T00:00:00.000"), (5, "LIMS", "Laboratory Information", "lims-primary", 1, "2026-08-24T00:00:00.000"), (6, "SCADA", "Plant Telemetry", "scada-primary", 1, "2026-08-24T00:00:00.000")]
    if table.name == "EtlSourceSystem":
        for system in systems:
            vals = dict(zip([c.name for c in cols], system))
            rows.append([vals[c.name] for c in cols])
    elif table.name == "SchemaVersion":
        vals = {"SchemaVersionId": 1, "VersionNumber": "1.0.0", "AppliedAtUtc": "2026-08-24T00:00:00.000", "AppliedByName": "tools.seeder", "ScriptName": "tools.seeder.cli", "ChecksumValue": "00" * 32}
        rows.append([vals.get(c.name, unknown_value(c)) for c in cols])
    elif table.name == "TableLoadConfig":
        loadable = [t for t in model.tables if t.kind in {"dim", "fact", "bridge"}]
        for idx, target in enumerate(sorted(loadable, key=lambda t: (t.schema, t.name)), start=1):
            pattern = "Fact" if target.kind == "fact" else ("Bridge" if target.kind == "bridge" else ("SCD2" if target.scd == "type2" else "Dimension"))
            vals = {"TableLoadConfigId": idx, "SourceObjectName": f"seed.{target.schema}.{target.name}", "TargetSchemaName": target.schema, "TargetTableName": target.name, "LoadPatternName": pattern, "IsEnabled": 1}
            rows.append([vals.get(c.name, unknown_value(c)) for c in cols])
    return rows


def _max_text_len(col) -> int | None:
    m = re.match(r"n?(?:var)?char\((\d+)\)", str(col.type).strip(), re.IGNORECASE)
    return int(m.group(1)) if m else None


def _enforce_unique_business_key(table: Table, cols, rows: list[list[Any]]) -> list[list[Any]]:
    """The generator puts a UNIQUE index on every business key, so seeded values must be
    unique. Value factories draw from finite vocabularies, so disambiguate deterministically."""
    if not table.business_key or table.kind not in {"ref", "dim", "bridge"}:
        return rows
    names = [c.name for c in cols]
    if table.business_key.name not in names:
        return rows
    idx = names.index(table.business_key.name)
    limit = _max_text_len(cols[idx])
    seen: set[str] = set()
    for row in rows:
        value = row[idx]
        if value is None:
            continue
        text = str(value)
        if text not in seen:
            seen.add(text)
            continue
        for n in range(2, 10_000_000):
            suffix = f"-{n}"
            candidate = text if limit is None else text[: max(1, limit - len(suffix))]
            candidate = f"{candidate}{suffix}"
            if limit is not None and len(candidate) > limit:
                candidate = candidate[-limit:]
            if candidate not in seen:
                seen.add(candidate)
                row[idx] = candidate
                break
        else:
            raise ValueError(f"cannot uniquify business key for {table.schema}.{table.name}")
    return rows


def _write_csv(path: Path, cols, rows_iter) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow([c.name for c in cols])
        for row in rows_iter:
            writer.writerow(row)
            count += 1
    return count


def _register_keys(table: Table, cols, path: Path, count: int, result: SeedResult) -> None:
    key_col = table.key_column if table.kind != "etl" else (table.primary_key[0] if table.primary_key else None)
    keys: set[int] = set()
    if key_col and table.kind in {"ref", "dim", "bridge"}:
        idx = [c.name for c in cols].index(key_col)
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                try:
                    keys.add(int(row[idx]))
                except (ValueError, IndexError):
                    pass
        result.key_sets[(table.schema, table.name)] = keys


def _date_window() -> list[int]:
    end = min(dt.date.today(), DATE_END)
    start = max(DATE_START, end - dt.timedelta(days=89))
    keys = []
    cur = start
    while cur <= end:
        keys.append(int(cur.strftime("%Y%m%d")))
        cur += dt.timedelta(days=1)
    return keys


def _ordered_tables(model: Model) -> list[Table]:
    ordered = [t for t in topological_sort(model) if t.kind != "stg"]
    refs = [t for t in ordered if t.kind == "ref"]
    dims = [t for t in ordered if t.kind == "dim"]
    bridges = [t for t in ordered if t.kind == "bridge"]
    facts = [t for t in ordered if t.kind == "fact"]
    etl = [t for t in ordered if t.kind == "etl"]
    return refs + dims + bridges + facts + etl


def _write_sql_script(sql_path: Path, table: Table, csv_rel: str, has_identity: bool) -> None:
    full = f"{q(table.schema)}.{q(table.name)}"
    use_identity = has_identity and table.kind in {"ref", "dim", "bridge"}
    id_on = f"    SET IDENTITY_INSERT {full} ON;\n" if use_identity else ""
    id_off = f"    SET IDENTITY_INSERT {full} OFF;\n" if use_identity else ""
    keep = ", KEEPIDENTITY" if use_identity else ""

    # BULK INSERT options must be literals, and CODEPAGE is Windows-only while UTF-8 CSVs
    # need it there. Build the statement dynamically so one script works on both platforms.
    def lit(s: str) -> str:
        return s.replace("'", "''")

    head = (
        f"BULK INSERT {full}\n"
        f"    FROM '$(SeedRoot)/{csv_rel}'\n"
        "    WITH (FORMAT = 'CSV', FIRSTROW = 2, FIELDQUOTE = '\"', "
        "ROWTERMINATOR = '0x0a', DATAFILETYPE = 'char'"
    )
    tail = f"{keep}, TABLOCK);"

    body = f"""-- Generated by tools.seeder. Do not hand-edit.
-- Requires sqlcmd variable SeedRoot, e.g. -v SeedRoot="C:\\path\\to\\seed" (or /path/to/seed).

IF OBJECT_ID(N'{table.schema}.{table.name}', N'U') IS NOT NULL
AND NOT EXISTS (SELECT 1 FROM {full})
BEGIN
    DECLARE @sql nvarchar(max) =
        N'{lit(head)}'
        + CASE WHEN SERVERPROPERTY('HostPlatform') = N'Windows'
               THEN N', CODEPAGE = ''65001''' ELSE N'' END
        + N'{lit(tail)}';
    BEGIN TRY
{id_on}        EXEC sys.sp_executesql @sql;
{id_off}    END TRY
    BEGIN CATCH
{id_off}        THROW;
    END CATCH
END
GO
"""
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    sql_path.write_text(body, encoding="utf-8", newline="\n")


RECHECK_SCRIPT_NAME = "9999_recheck_constraints.sql"

RECHECK_SCRIPT = """-- Generated by tools.seeder. Do not hand-edit.
-- BULK INSERT skips FK/CHECK validation and leaves those constraints NOT TRUSTED.
-- Re-validate every constraint so the optimizer can rely on them and so the seed
-- data is proven referentially sound.
DECLARE @name sysname, @schema sysname, @sql nvarchar(max);
DECLARE recheck CURSOR LOCAL FAST_FORWARD FOR
    SELECT OBJECT_SCHEMA_NAME(parent_object_id), OBJECT_NAME(parent_object_id)
    FROM sys.foreign_keys WHERE is_not_trusted = 1
    UNION
    SELECT OBJECT_SCHEMA_NAME(parent_object_id), OBJECT_NAME(parent_object_id)
    FROM sys.check_constraints WHERE is_not_trusted = 1;
OPEN recheck;
FETCH NEXT FROM recheck INTO @schema, @name;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'ALTER TABLE ' + QUOTENAME(@schema) + N'.' + QUOTENAME(@name)
             + N' WITH CHECK CHECK CONSTRAINT ALL;';
    EXEC sys.sp_executesql @sql;
    FETCH NEXT FROM recheck INTO @schema, @name;
END
CLOSE recheck;
DEALLOCATE recheck;

DECLARE @untrusted int =
    (SELECT COUNT(*) FROM sys.foreign_keys WHERE is_not_trusted = 1)
  + (SELECT COUNT(*) FROM sys.check_constraints WHERE is_not_trusted = 1);
IF @untrusted > 0
    RAISERROR('Seed verification failed: %d constraint(s) remain untrusted.', 16, 1, @untrusted);
ELSE
    PRINT 'All foreign key and check constraints are trusted.';
GO
"""


def _write_manifest(sql_out: Path, scripts: list[Path]) -> None:
    (sql_out / RECHECK_SCRIPT_NAME).write_text(RECHECK_SCRIPT, encoding="utf-8", newline="\n")
    lines = [
        "-- Generated by tools.seeder. Do not hand-edit.",
        '-- Requires sqlcmd variable SeedRoot, e.g. -v SeedRoot="C:\\path\\to\\seed" (or /path/to/seed).',
        "",
    ]
    lines.extend(f":r {p.name}" for p in scripts if p.name != "00_seed_manifest.sql")
    lines.append(f":r {RECHECK_SCRIPT_NAME}")
    (sql_out / "00_seed_manifest.sql").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def generate_seed(model_root: str | Path = "model", out: str | Path = "seed", sql_out: str | Path = "sql\\95_seed", scale: float = 1.0) -> SeedResult:
    start = time.perf_counter()
    model = load_model(model_root)
    out = Path(out)
    sql_out = Path(sql_out)
    _clean_dir(out, ".csv")
    _clean_dir(sql_out, ".sql")
    result = SeedResult()
    fact_rows = _fact_distribution(model, scale)
    scripts: list[Path] = []
    window = _date_window()

    for ordinal, table in enumerate(_ordered_tables(model), start=1):
        if table.kind == "stg":
            continue
        cols = table_columns(table)
        path = _csv_path(out, table)
        rng = table_rng(f"{table.schema}.{table.name}")
        count = _row_count(table, scale, model, fact_rows)
        if table.kind == "ref":
            base_rows = _seed_value_rows(table, cols, result, rng) if table.seed_values else [_normal_row(table, cols, i, result, rng) for i in range(1, count + 1)]
            rows = _enforce_unique_business_key(table, cols, [_unknown_row(table, cols)] + base_rows)
            written = _write_csv(path, cols, rows)
        elif table.kind == "dim" and table.name == "Date":
            written = _write_csv(path, cols, _date_rows(table, cols, result, rng))
        elif table.kind == "dim" and table.name == "TimeOfDay":
            written = _write_csv(path, cols, _time_rows(table, cols, result, rng))
        elif table.kind == "dim":
            rows = _enforce_unique_business_key(table, cols, [_unknown_row(table, cols)] + [_normal_row(table, cols, i, result, rng) for i in range(1, count + 1)])
            written = _write_csv(path, cols, rows)
        elif table.kind == "bridge":
            rows = _enforce_unique_business_key(table, cols, [_unknown_row(table, cols)] + _bridge_rows(table, cols, count, result, rng))
            written = _write_csv(path, cols, rows)
        elif table.kind == "fact":
            written = _write_csv(path, cols, (_fact_row(table, cols, i, result, rng, window) for i in range(1, count + 1)))
        elif table.kind == "etl":
            rows = _etl_rows(table, model)
            if not rows:
                continue
            written = _write_csv(path, cols, rows)
        else:
            continue
        result.rows_by_table[(table.schema, table.name)] = written
        result.rows_by_schema[table.schema] = result.rows_by_schema.get(table.schema, 0) + written
        result.csv_paths[(table.schema, table.name)] = path
        _register_keys(table, cols, path, written, result)
        csv_rel = f"{table.schema}/{table.name}.csv"
        has_identity = any(c.identity for c in cols)
        script = sql_out / f"{ordinal:04d}_{table.schema}_{table.name}.sql"
        _write_sql_script(script, table, csv_rel, has_identity)
        scripts.append(script)

    _write_manifest(sql_out, scripts)
    result.elapsed_seconds = time.perf_counter() - start
    return result
