from __future__ import annotations

from .. import db, discovery
from ..etl_control import BatchContext, log_scd_change


def _row_hash_expr(alias: str, columns: list[str]) -> str:
    parts = ", '|', ".join(f"CONVERT(nvarchar(max), {alias}.{db.quote_name(c)})" for c in columns)
    return f"HASHBYTES('SHA2_256', CONCAT({parts}))" if parts else "HASHBYTES('SHA2_256', '')"


def build_scd2_merge_sql(table: str, business_key: str, source_columns: list[str], target_columns: list[str]) -> str:
    attrs = [c for c in source_columns if c not in {"BatchId", "RowNumber", business_key, "RowHash"} and c in target_columns]
    hash_expr = "s.[RowHash]" if "RowHash" in source_columns else _row_hash_expr("s", [business_key] + attrs)
    insert_cols = [
        business_key,
        *attrs,
        "SourceSystemKey",
        "BatchId",
        "EffectiveFromUtc",
        "EffectiveToUtc",
        "IsCurrent",
        "RowHash",
    ]
    insert_cols = [c for c in insert_cols if c in target_columns]
    insert_vals = []
    for c in insert_cols:
        if c == "SourceSystemKey":
            insert_vals.append("-1")
        elif c == "BatchId":
            insert_vals.append("?")
        elif c == "EffectiveFromUtc":
            insert_vals.append("SYSUTCDATETIME()")
        elif c == "EffectiveToUtc":
            insert_vals.append("CONVERT(datetime2(3), '9999-12-31')")
        elif c == "IsCurrent":
            insert_vals.append("1")
        elif c == "RowHash":
            insert_vals.append(hash_expr)
        else:
            insert_vals.append(f"s.{db.quote_name(c)}")
    return f"""
IF OBJECT_ID('tempdb..#Changed_{table}') IS NOT NULL DROP TABLE #Changed_{table};
SELECT s.*, {hash_expr} AS ComputedRowHash
INTO #Changed_{table}
FROM [stg].{db.quote_name(table)} s
LEFT JOIN [dim].{db.quote_name(table)} t
  ON t.{db.quote_name(business_key)} = s.{db.quote_name(business_key)} AND t.IsCurrent = 1
WHERE s.BatchId = ? AND (t.{db.quote_name(business_key)} IS NULL OR t.RowHash <> {hash_expr});

UPDATE t
   SET EffectiveToUtc = SYSUTCDATETIME(), IsCurrent = 0
FROM [dim].{db.quote_name(table)} t
JOIN #Changed_{table} s ON t.{db.quote_name(business_key)} = s.{db.quote_name(business_key)}
WHERE t.IsCurrent = 1;

INSERT INTO [dim].{db.quote_name(table)} ({', '.join(db.quote_name(c) for c in insert_cols)})
SELECT {', '.join(insert_vals)}
FROM #Changed_{table} s;

DROP TABLE #Changed_{table};
""".strip()


def build_type1_merge_sql(table: str, business_key: str, source_columns: list[str], target_columns: list[str]) -> str:
    attrs = [c for c in source_columns if c not in {"BatchId", "RowNumber", business_key} and c in target_columns]
    updates = ", ".join(f"t.{db.quote_name(c)} = s.{db.quote_name(c)}" for c in attrs)
    insert_cols = [business_key, *attrs, *[c for c in ("SourceSystemKey", "BatchId") if c in target_columns]]
    insert_vals = [f"s.{db.quote_name(business_key)}", *[f"s.{db.quote_name(c)}" for c in attrs]]
    insert_vals += ["-1" if c == "SourceSystemKey" else "?" for c in ("SourceSystemKey", "BatchId") if c in target_columns]
    update_clause = f"WHEN MATCHED THEN UPDATE SET {updates}" if updates else ""
    return f"""
MERGE [dim].{db.quote_name(table)} AS t
USING (SELECT * FROM [stg].{db.quote_name(table)} WHERE BatchId = ?) AS s
ON t.{db.quote_name(business_key)} = s.{db.quote_name(business_key)}
{update_clause}
WHEN NOT MATCHED BY TARGET THEN
  INSERT ({', '.join(db.quote_name(c) for c in insert_cols)}) VALUES ({', '.join(insert_vals)});
""".strip()


def run(ctx: BatchContext, conn) -> dict[str, int]:
    counts: dict[str, int] = {}
    stg_tables = set(discovery.list_tables(conn, "stg")) if discovery.schema_exists(conn, "stg") else set()
    configs = discovery.table_load_config(conn)
    for table in sorted(stg_tables):
        if not discovery.table_exists(conn, "dim", table):
            continue
        stg_cols = discovery.get_column_names(conn, "stg", table)
        dim_cols = discovery.get_column_names(conn, "dim", table)
        business_key = configs.get(("dim", table), {}).get("BusinessKey") or discovery.get_business_key(conn, "dim", table)
        if not business_key or business_key not in stg_cols:
            ctx.logger.warning("dimension_business_key_missing", table=table)
            continue
        is_scd2 = all(c in dim_cols for c in ("EffectiveFromUtc", "EffectiveToUtc", "IsCurrent", "RowHash"))
        sql = build_scd2_merge_sql(table, business_key, stg_cols, dim_cols) if is_scd2 else build_type1_merge_sql(table, business_key, stg_cols, dim_cols)
        row_count = int(db.query(conn, f"SELECT COUNT_BIG(*) AS Cnt FROM [stg].{db.quote_name(table)} WHERE BatchId = ?", [ctx.batch_id])[0]["Cnt"])
        db.execute(conn, sql, [ctx.batch_id, ctx.batch_id])
        if is_scd2:
            for row in db.query(conn, f"SELECT DISTINCT {db.quote_name(business_key)} AS BusinessKey FROM [stg].{db.quote_name(table)} WHERE BatchId = ?", [ctx.batch_id]):
                log_scd_change(conn, ctx.batch_id, table, str(row["BusinessKey"]), "upsert")
        counts[f"dim.{table}"] = row_count
    ctx.row_counts.update(counts)
    return counts
