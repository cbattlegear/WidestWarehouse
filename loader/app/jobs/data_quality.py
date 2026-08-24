from __future__ import annotations

from .. import db, discovery
from ..etl_control import BatchContext, write_dq_result


def run(ctx: BatchContext, conn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for schema in ("dim", "fact", "stg"):
        if not discovery.schema_exists(conn, schema):
            continue
        for table in discovery.list_tables(conn, schema):
            full = f"{schema}.{table}"
            cnt = int(db.query(conn, f"SELECT COUNT_BIG(*) AS Cnt FROM {db.quote_name(schema)}.{db.quote_name(table)}")[0]["Cnt"])
            write_dq_result(conn, ctx.batch_id, "Row count", full, cnt >= 0, cnt)
            counts[f"dq.row_count.{full}"] = cnt
            cols = discovery.get_column_names(conn, schema, table)
            for col in [c for c in cols if c.endswith("Key") and c != f"{table}Key"]:
                nulls = int(
                    db.query(
                        conn,
                        f"SELECT COUNT_BIG(*) AS Cnt FROM {db.quote_name(schema)}.{db.quote_name(table)} WHERE {db.quote_name(col)} IS NULL",
                    )[0]["Cnt"]
                )
                write_dq_result(conn, ctx.batch_id, f"Null key {col}", full, nulls == 0, nulls)
            if schema == "fact":
                for col in [c for c in cols if any(token in c for token in ("Amount", "Quantity", "Cost", "Minutes", "Hours", "Weight"))]:
                    negatives = int(
                        db.query(
                            conn,
                            f"SELECT COUNT_BIG(*) AS Cnt FROM {db.quote_name(schema)}.{db.quote_name(table)} WHERE {db.quote_name(col)} < 0",
                        )[0]["Cnt"]
                    )
                    write_dq_result(conn, ctx.batch_id, f"Negative measure {col}", full, negatives == 0, negatives)
    for fact in discovery.list_tables(conn, "fact") if discovery.schema_exists(conn, "fact") else []:
        for fk in discovery.fact_foreign_keys(conn, fact):
            sql = f"""
                SELECT COUNT_BIG(*) AS Cnt
                FROM [fact].{db.quote_name(fact)} f
                LEFT JOIN {db.quote_name(fk.parent_schema)}.{db.quote_name(fk.parent_table)} p
                  ON p.{db.quote_name(fk.parent_column)} = f.{db.quote_name(fk.child_column)}
                WHERE f.{db.quote_name(fk.child_column)} <> -1 AND p.{db.quote_name(fk.parent_column)} IS NULL
            """
            orphans = int(db.query(conn, sql)[0]["Cnt"])
            write_dq_result(conn, ctx.batch_id, f"Orphan FK {fk.child_column}", f"fact.{fact}", orphans == 0, orphans)
    ctx.row_counts.update(counts)
    return counts
