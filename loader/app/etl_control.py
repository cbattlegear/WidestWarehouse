from __future__ import annotations

import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from . import db, discovery


class MissingWarehouseSchema(RuntimeError):
    pass


@dataclass
class BatchContext:
    config: Any
    batch_id: int
    job_name: str
    landing_path: Any
    logger: Any
    row_counts: dict[str, int] = field(default_factory=dict)


def unique_batch_id() -> int:
    return int(time.time() * 1000)


def _ensure_table(conn, schema: str, table: str) -> None:
    if not discovery.table_exists(conn, schema, table):
        raise MissingWarehouseSchema(
            f"Required table {schema}.{table} does not exist. Deploy the warehouse schema and the loader will retry."
        )


def _columns(conn, schema: str, table: str) -> set[str]:
    return set(discovery.get_column_names(conn, schema, table))


def _insert_available(conn, schema: str, table: str, values: dict[str, Any]) -> None:
    cols = [c for c in values if c in _columns(conn, schema, table)]
    if not cols:
        return
    sql = (
        f"INSERT INTO {db.quote_name(schema)}.{db.quote_name(table)} "
        f"({', '.join(db.quote_name(c) for c in cols)}) VALUES ({', '.join('?' for _ in cols)})"
    )
    db.execute(conn, sql, [values[c] for c in cols])


def open_batch_run(conn, job_name: str) -> int:
    _ensure_table(conn, "etl", "BatchRun")
    cols = _columns(conn, "etl", "BatchRun")
    now = datetime.now(timezone.utc)
    values = {
        "JobName": job_name,
        "PipelineName": job_name,
        "SourceSystemCode": "LOADER",
        "BatchStatusCode": "Running",
        "Status": "Running",
        "StartedAtUtc": now,
        "StartTimeUtc": now,
        "CreatedAtUtc": now,
        "RequestedByName": f"loader:{job_name}"[:100],
        "CorrelationId": str(uuid.uuid4()),
    }
    writable = [c for c in values if c in cols]
    if "BatchId" in cols:
        sql = (
            f"INSERT INTO etl.BatchRun ({', '.join(db.quote_name(c) for c in writable)}) "
            f"OUTPUT INSERTED.BatchId VALUES ({', '.join('?' for _ in writable)})"
        )
        rows = db.query(conn, sql, [values[c] for c in writable])
        return int(rows[0]["BatchId"]) if rows else unique_batch_id()
    _insert_available(conn, "etl", "BatchRun", values)
    return unique_batch_id()


def close_batch_run(
    conn,
    batch_id: int,
    status: str,
    row_counts: dict[str, int] | None = None,
    message: str | None = None,
) -> None:
    if not discovery.table_exists(conn, "etl", "BatchRun"):
        return
    cols = _columns(conn, "etl", "BatchRun")
    now = datetime.now(timezone.utc)
    updates: dict[str, Any] = {
        "Status": status,
        "BatchStatusCode": status,
        "EndedAtUtc": now,
        "EndTimeUtc": now,
        "CompletedAtUtc": now,
    }
    if "RowsProcessed" in cols and row_counts:
        updates["RowsProcessed"] = sum(row_counts.values())
    if "ErrorMessage" in cols and message:
        updates["ErrorMessage"] = message[:4000]
    set_cols = [c for c in updates if c in cols]
    if set_cols and "BatchId" in cols:
        db.execute(
            conn,
            f"UPDATE etl.BatchRun SET {', '.join(db.quote_name(c) + ' = ?' for c in set_cols)} WHERE BatchId = ?",
            [updates[c] for c in set_cols] + [batch_id],
        )


def record_step(conn, batch_id: int, step_name: str, status: str, row_count: int = 0, message: str | None = None) -> None:
    if not discovery.table_exists(conn, "etl", "BatchRunStep"):
        return
    now = datetime.now(timezone.utc)
    _insert_available(
        conn,
        "etl",
        "BatchRunStep",
        {
            "BatchId": batch_id,
            "StepName": step_name,
            "StepStatusCode": status,
            "Status": status,
            "RowCount": row_count,
            "StartedAtUtc": now,
            "EndedAtUtc": now,
            "Message": message,
            "ErrorMessage": message,
        },
    )


def _watermark_parts(process_name: str) -> tuple[str, str, str]:
    schema_name, _, table_name = process_name.partition(".")
    if not table_name:
        schema_name, table_name = "etl", process_name
    return "LOADER", schema_name[:128], table_name[:128]


def get_watermark(conn, process_name: str) -> str | None:
    if not discovery.table_exists(conn, "etl", "LoadWatermark"):
        return None
    source, schema_name, table_name = _watermark_parts(process_name)
    rows = db.query(
        conn,
        "SELECT TOP (1) WatermarkValue FROM etl.LoadWatermark "
        "WHERE SourceSystemCode = ? AND SchemaName = ? AND TableName = ? "
        "ORDER BY WatermarkId DESC",
        [source, schema_name, table_name],
    )
    return str(rows[0]["WatermarkValue"]) if rows and rows[0]["WatermarkValue"] is not None else None


def update_watermark(conn, process_name: str, value: str, batch_id: int = 0) -> None:
    if not discovery.table_exists(conn, "etl", "LoadWatermark"):
        return
    source, schema_name, table_name = _watermark_parts(process_name)
    db.execute(
        conn,
        """
        MERGE etl.LoadWatermark AS tgt
        USING (SELECT ? AS SourceSystemCode, ? AS SchemaName, ? AS TableName) AS src
        ON  tgt.SourceSystemCode = src.SourceSystemCode
        AND tgt.SchemaName = src.SchemaName
        AND tgt.TableName = src.TableName
        WHEN MATCHED THEN UPDATE SET WatermarkValue = ?, BatchId = ?
        WHEN NOT MATCHED THEN
            INSERT (BatchId, SourceSystemCode, SchemaName, TableName, WatermarkColumnName, WatermarkValue)
            VALUES (?, src.SourceSystemCode, src.SchemaName, src.TableName, 'LoadedAtUtc', ?);
        """,
        [source, schema_name, table_name, value, batch_id, batch_id, value],
    )


def log_error(conn, batch_id: int, job_name: str, error: BaseException | str, table_name: str | None = None) -> None:
    if not discovery.table_exists(conn, "etl", "LoadError"):
        return
    schema_name, _, bare_table = (table_name or "").partition(".")
    if not bare_table:
        schema_name, bare_table = "etl", (table_name or job_name)
    _insert_available(
        conn,
        "etl",
        "LoadError",
        {
            "BatchId": batch_id,
            "JobName": job_name,
            "SchemaName": schema_name[:128],
            "TableName": bare_table[:128],
            "ErrorMessage": str(error)[:4000],
            "OccurredAtUtc": datetime.now(timezone.utc),
            "ErrorUtc": datetime.now(timezone.utc),
            "CreatedAtUtc": datetime.now(timezone.utc),
        },
    )


def _ensure_dq_rule(conn, rule_name: str, target_table: str) -> int | None:
    """DqResult.DqRuleId is a NOT NULL FK, so the rule must exist before results are written."""
    if not discovery.table_exists(conn, "etl", "DqRule"):
        return None
    rule_code = re.sub(r"[^A-Za-z0-9]+", "_", f"{rule_name}_{target_table}").strip("_").upper()[:50]
    rows = db.query(conn, "SELECT TOP (1) DqRuleId FROM etl.DqRule WHERE RuleCode = ?", [rule_code])
    if rows:
        return int(rows[0]["DqRuleId"])
    cols = _columns(conn, "etl", "DqRule")
    values = {
        "RuleCode": rule_code,
        "RuleName": rule_name[:200],
        "TargetTableName": target_table[:257],
        "RuleExpression": f"{rule_name} on {target_table}"[:500],
        "IsActive": 1,
    }
    writable = [c for c in values if c in cols]
    inserted = db.query(
        conn,
        f"INSERT INTO etl.DqRule ({', '.join(db.quote_name(c) for c in writable)}) "
        f"OUTPUT INSERTED.DqRuleId VALUES ({', '.join('?' for _ in writable)})",
        [values[c] for c in writable],
    )
    return int(inserted[0]["DqRuleId"]) if inserted else None


def write_dq_result(
    conn,
    batch_id: int,
    rule_name: str,
    target_table: str,
    passed: bool,
    observed_value: int | float | str,
    message: str | None = None,
) -> None:
    if not discovery.table_exists(conn, "etl", "DqResult"):
        return
    try:
        failed_rows = 0 if passed else max(int(float(observed_value)), 1)
    except (TypeError, ValueError):
        failed_rows = 0 if passed else 1
    _insert_available(
        conn,
        "etl",
        "DqResult",
        {
            "BatchId": batch_id,
            "DqRuleId": _ensure_dq_rule(conn, rule_name, target_table),
            "RuleName": rule_name,
            "TargetTable": target_table,
            "CheckedRowCount": failed_rows,
            "FailedRowCount": failed_rows,
            "ResultStatusCode": "Pass" if passed else "Fail",
            "Status": "Pass" if passed else "Fail",
            "Passed": int(passed),
            "ObservedValue": str(observed_value),
            "Message": message,
            "CheckedAtUtc": datetime.now(timezone.utc),
            "CreatedAtUtc": datetime.now(timezone.utc),
        },
    )


def log_scd_change(conn, batch_id: int, table_name: str, business_key: str, change_type: str) -> None:
    if not discovery.table_exists(conn, "etl", "ScdChangeLog"):
        return
    _insert_available(
        conn,
        "etl",
        "ScdChangeLog",
        {
            "BatchId": batch_id,
            "DimensionName": table_name,
            "TableName": table_name,
            "BusinessKeyValue": business_key,
            "BusinessKey": business_key,
            "ChangeTypeCode": change_type,
            "ChangeType": change_type,
            "ChangedAtUtc": datetime.now(timezone.utc),
            "CreatedAtUtc": datetime.now(timezone.utc),
        },
    )


@contextmanager
def process_lock(conn, name: str, timeout_ms: int = 0) -> Iterator[None]:
    rows = db.query(
        conn,
        "DECLARE @r int; EXEC @r = sp_getapplock @Resource=?, @LockMode='Exclusive', @LockOwner='Session', @LockTimeout=?; SELECT @r AS ResultCode;",
        [name, timeout_ms],
    )
    code = int(rows[0]["ResultCode"]) if rows else -999
    if code < 0:
        raise RuntimeError(f"Could not acquire process lock {name}; result code {code}")
    try:
        yield
    finally:
        db.execute(conn, "EXEC sp_releaseapplock @Resource=?, @LockOwner='Session';", [name])
