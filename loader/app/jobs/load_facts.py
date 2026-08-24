from __future__ import annotations

from dataclasses import dataclass

from .. import db, discovery
from ..etl_control import BatchContext, write_dq_result


@dataclass(frozen=True)
class LookupMap:
    fact_key_column: str
    parent_schema: str
    parent_table: str
    parent_key_column: str
    parent_business_key: str
    stage_natural_column: str
    parent_is_scd2: bool = False


@dataclass(frozen=True)
class KeyPassThrough:
    """A fact FK column staged as a raw surrogate key with no natural-key lookup."""

    fact_key_column: str
    parent_schema: str
    parent_table: str
    parent_key_column: str


def build_fact_insert_sql(
    fact_table: str,
    stage_columns: list[str],
    target_columns: list[str],
    lookups: list[LookupMap],
    key_checks: list[KeyPassThrough] | None = None,
) -> tuple[str, str]:
    """Return the (delete, insert) statements. They must be executed separately:
    a multi-statement batch with parameters silently binds the wrong values and
    inserts zero rows."""
    key_checks = key_checks or []
    lookup_by_key = {l.fact_key_column: l for l in lookups}
    check_by_key = {k.fact_key_column: k for k in key_checks if k.fact_key_column not in lookup_by_key}
    degenerate = [c for c in stage_columns if c not in {"BatchId", "RowNumber"} and c in target_columns and c not in lookup_by_key]
    insert_cols = [c for c in target_columns if c in lookup_by_key or c in degenerate or c in {"SourceSystemKey", "BatchId"}]
    joins = []
    select_exprs = []
    for idx, lk in enumerate(lookups):
        alias = f"lk{idx}"
        current = f" AND {alias}.IsCurrent = 1" if lk.parent_is_scd2 else ""
        joins.append(
            f"LEFT JOIN {db.quote_name(lk.parent_schema)}.{db.quote_name(lk.parent_table)} {alias} "
            f"ON {alias}.{db.quote_name(lk.parent_business_key)} = s.{db.quote_name(lk.stage_natural_column)}{current}"
        )
    check_alias: dict[str, str] = {}
    for idx, kc in enumerate(k for k in check_by_key.values() if k.fact_key_column in insert_cols):
        alias = f"kv{idx}"
        check_alias[kc.fact_key_column] = alias
        joins.append(
            f"LEFT JOIN {db.quote_name(kc.parent_schema)}.{db.quote_name(kc.parent_table)} {alias} "
            f"ON {alias}.{db.quote_name(kc.parent_key_column)} = s.{db.quote_name(kc.fact_key_column)}"
        )
    for c in insert_cols:
        if c in lookup_by_key:
            lk = lookup_by_key[c]
            alias = f"lk{lookups.index(lk)}"
            select_exprs.append(f"COALESCE({alias}.{db.quote_name(lk.parent_key_column)}, -1) AS {db.quote_name(c)}")
        elif c in check_alias:
            kc = check_by_key[c]
            select_exprs.append(f"COALESCE({check_alias[c]}.{db.quote_name(kc.parent_key_column)}, -1) AS {db.quote_name(c)}")
        elif c == "SourceSystemKey":
            select_exprs.append("-1 AS [SourceSystemKey]")
        elif c == "BatchId":
            select_exprs.append("? AS [BatchId]")
        else:
            select_exprs.append(f"s.{db.quote_name(c)}")
    delete_sql = f"DELETE FROM [fact].{db.quote_name(fact_table)} WHERE BatchId = ?;"
    insert_sql = f"""
INSERT INTO [fact].{db.quote_name(fact_table)} ({', '.join(db.quote_name(c) for c in insert_cols)})
SELECT {', '.join(select_exprs)}
FROM [stg].{db.quote_name(fact_table)} s
{' '.join(joins)}
WHERE s.BatchId = ?;
""".strip()
    return delete_sql, insert_sql


def discover_lookup_maps(conn, fact_table: str, stage_columns: list[str]) -> tuple[list[LookupMap], list[KeyPassThrough]]:
    maps: list[LookupMap] = []
    checks: list[KeyPassThrough] = []
    for fk in discovery.fact_foreign_keys(conn, fact_table):
        bk = discovery.get_business_key(conn, fk.parent_schema, fk.parent_table)
        natural = f"{fk.parent_table}{bk}" if bk else None
        if bk and natural in stage_columns:
            parent_cols = discovery.get_column_names(conn, fk.parent_schema, fk.parent_table)
            is_scd2 = "IsCurrent" in parent_cols
            maps.append(LookupMap(fk.child_column, fk.parent_schema, fk.parent_table, fk.parent_column, bk, natural, is_scd2))
        elif fk.child_column in stage_columns:
            # Staged as a raw surrogate key (date/time/role-played). It must still be
            # validated against the parent or the insert fails the foreign key.
            checks.append(KeyPassThrough(fk.child_column, fk.parent_schema, fk.parent_table, fk.parent_column))
    return maps, checks


def run(ctx: BatchContext, conn) -> dict[str, int]:
    counts: dict[str, int] = {}
    stg_tables = set(discovery.list_tables(conn, "stg")) if discovery.schema_exists(conn, "stg") else set()
    for table in sorted(stg_tables):
        if not discovery.table_exists(conn, "fact", table):
            continue
        stage_columns = discovery.get_column_names(conn, "stg", table)
        target_columns = discovery.get_column_names(conn, "fact", table)
        lookups, key_checks = discover_lookup_maps(conn, table, stage_columns)
        delete_sql, insert_sql = build_fact_insert_sql(table, stage_columns, target_columns, lookups, key_checks)
        db.execute(conn, delete_sql, [ctx.batch_id])
        try:
            inserted = db.execute(conn, insert_sql, [ctx.batch_id, ctx.batch_id])
        except Exception as exc:
            raise RuntimeError(f"fact load failed for fact.{table}: {exc}") from exc
        counts[f"fact.{table}"] = max(inserted, 0)
        for lk in lookups:
            miss_sql = f"""
                SELECT COUNT_BIG(*) AS Cnt
                FROM [stg].{db.quote_name(table)} s
                LEFT JOIN {db.quote_name(lk.parent_schema)}.{db.quote_name(lk.parent_table)} p
                  ON p.{db.quote_name(lk.parent_business_key)} = s.{db.quote_name(lk.stage_natural_column)}
                WHERE s.BatchId = ? AND s.{db.quote_name(lk.stage_natural_column)} IS NOT NULL
                  AND p.{db.quote_name(lk.parent_key_column)} IS NULL
            """
            misses = int(db.query(conn, miss_sql, [ctx.batch_id])[0]["Cnt"])
            if misses:
                write_dq_result(
                    conn,
                    ctx.batch_id,
                    f"Missing lookup {lk.fact_key_column}",
                    f"fact.{table}",
                    False,
                    misses,
                    "Loaded with -1 Unknown fallback",
                )
    ctx.row_counts.update(counts)
    return counts
