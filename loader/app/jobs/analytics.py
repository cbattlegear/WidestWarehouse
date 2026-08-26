"""Analytics workload: a fixed set of stored procedures plus randomized ad-hoc queries.

The procedures in the reporting schema always run the same statements, which makes their
timings comparable between cycles. The randomized half generates plausible BI-style
queries against whatever star/snowflake shape is actually deployed. Neither writes to the
database: this job is read-only by construction.

Every identifier comes from the SQL Server catalog, so the generated SQL is always
valid for the deployed schema and never contains caller-supplied text.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .. import db, discovery
from ..etl_control import BatchContext

# Columns that carry no analytical meaning as a grouping attribute.
SYSTEM_COLUMNS = {
    "BatchId",
    "LoadedAtUtc",
    "UpdatedAtUtc",
    "RowHash",
    "IsCurrent",
    "EffectiveFromUtc",
    "EffectiveToUtc",
    "SourceSystemKey",
    "RowNumber",
}

TEXT_TYPES = {"char", "nchar", "varchar", "nvarchar"}
NUMERIC_TYPES = {"int", "bigint", "smallint", "tinyint", "decimal", "numeric", "money", "float", "real"}

AGGREGATES = ("SUM", "AVG", "MIN", "MAX")


@dataclass
class SchemaCache:
    """Lazily discovered catalog metadata, shared across the queries in one run."""

    conn: object
    _columns: dict[tuple[str, str], list[discovery.ColumnInfo]] = field(default_factory=dict)
    _fks: dict[tuple[str, str], list[discovery.ForeignKeyInfo]] = field(default_factory=dict)

    def columns(self, schema: str, table: str) -> list[discovery.ColumnInfo]:
        key = (schema, table)
        if key not in self._columns:
            self._columns[key] = discovery.get_columns(self.conn, schema, table)
        return self._columns[key]

    def foreign_keys(self, schema: str, table: str) -> list[discovery.ForeignKeyInfo]:
        key = (schema, table)
        if key not in self._fks:
            self._fks[key] = discovery.outgoing_foreign_keys(self.conn, schema, table)
        return self._fks[key]


@dataclass(frozen=True)
class GeneratedQuery:
    shape: str
    fact_table: str
    sql: str
    depth: int


def _attribute_columns(cache: SchemaCache, schema: str, table: str) -> list[str]:
    return [
        c.name
        for c in cache.columns(schema, table)
        if c.data_type in TEXT_TYPES and not c.name.endswith("Key") and c.name not in SYSTEM_COLUMNS
    ]


def _measure_columns(cache: SchemaCache, table: str) -> list[discovery.ColumnInfo]:
    return [
        c
        for c in cache.columns("fact", table)
        if c.data_type in NUMERIC_TYPES and not c.name.endswith("Key") and c.name not in SYSTEM_COLUMNS
    ]


def _date_key_columns(cache: SchemaCache, table: str) -> list[str]:
    return [c.name for c in cache.columns("fact", table) if c.name.endswith("DateKey")]


def _walk_branch(
    cache: SchemaCache,
    rng: random.Random,
    fact_table: str,
    max_depth: int,
) -> list[discovery.ForeignKeyInfo]:
    """Pick one FK out of the fact, then climb random parents up the snowflake.

    Tables already on the path are never revisited, which keeps self-referencing
    dimensions (a parent-product key, for example) from looping forever.
    """
    fks = cache.foreign_keys("fact", fact_table)
    if not fks:
        return []
    first = rng.choice(fks)
    path = [first]
    visited = {("fact", fact_table), (first.parent_schema, first.parent_table)}
    target_depth = rng.randint(1, max_depth)
    while len(path) < target_depth:
        tail = path[-1]
        candidates = [
            fk
            for fk in cache.foreign_keys(tail.parent_schema, tail.parent_table)
            if (fk.parent_schema, fk.parent_table) not in visited
        ]
        if not candidates:
            break
        step = rng.choice(candidates)
        path.append(step)
        visited.add((step.parent_schema, step.parent_table))
    return path


def _join_clauses(path: list[discovery.ForeignKeyInfo], prefix: str) -> tuple[list[str], str]:
    """Render a branch as INNER JOINs. Returns the clauses and the deepest alias."""
    clauses = []
    child_alias = "f"
    alias = child_alias
    for level, fk in enumerate(path):
        alias = f"{prefix}_{level}"
        clauses.append(
            f"INNER JOIN {db.quote_name(fk.parent_schema)}.{db.quote_name(fk.parent_table)} {alias} "
            f"ON {alias}.{db.quote_name(fk.parent_column)} = {child_alias}.{db.quote_name(fk.child_column)}"
        )
        child_alias = alias
    return clauses, alias


def build_random_query(cache: SchemaCache, rng: random.Random, fact_tables: list[str]) -> GeneratedQuery | None:
    fact_table = rng.choice(fact_tables)
    measures = _measure_columns(cache, fact_table)

    branch_count = rng.randint(1, 3)
    joins: list[str] = []
    group_exprs: list[str] = []
    select_exprs: list[str] = []
    max_depth_used = 0

    for branch in range(branch_count):
        path = _walk_branch(cache, rng, fact_table, max_depth=4)
        if not path:
            continue
        clauses, leaf_alias = _join_clauses(path, f"d{branch}")
        leaf = path[-1]
        attributes = _attribute_columns(cache, leaf.parent_schema, leaf.parent_table)
        if not attributes:
            continue
        joins.extend(clauses)
        attribute = rng.choice(attributes)
        expr = f"{leaf_alias}.{db.quote_name(attribute)}"
        label = f"{leaf.parent_table}_{attribute}"
        group_exprs.append(expr)
        select_exprs.append(f"{expr} AS {db.quote_name(label)}")
        max_depth_used = max(max_depth_used, len(path))

    if not group_exprs:
        return None

    shape = rng.choice(("aggregate_rollup", "top_n_measure", "distinct_count"))
    order_by = None

    if shape == "distinct_count" or not measures:
        shape = "distinct_count"
        select_exprs.append("COUNT_BIG(*) AS [FactRowCount]")
        select_exprs.append("COUNT(DISTINCT f.[BatchId]) AS [DistinctBatchCount]")
        order_by = "[FactRowCount] DESC"
    else:
        select_exprs.append("COUNT_BIG(*) AS [FactRowCount]")
        chosen = rng.sample(measures, k=min(len(measures), rng.randint(1, 3)))
        for measure in chosen:
            func = rng.choice(AGGREGATES)
            label = f"{func.title()}_{measure.name}"
            select_exprs.append(f"{func}(f.{db.quote_name(measure.name)}) AS {db.quote_name(label)}")
            if order_by is None and shape == "top_n_measure":
                order_by = f"{db.quote_name(label)} DESC"
        if order_by is None:
            order_by = "[FactRowCount] DESC"

    where_clauses = ["f.[BatchId] > 0"]
    date_keys = _date_key_columns(cache, fact_table)
    if date_keys and rng.random() < 0.5:
        # Date keys are yyyymmdd integers. The window is anchored on the server's
        # current year so it always overlaps freshly loaded data, whatever the year is.
        years_back = rng.randint(1, 3)
        where_clauses.append(
            f"f.{db.quote_name(rng.choice(date_keys))} BETWEEN "
            f"(YEAR(GETDATE()) - {years_back}) * 10000 + 101 AND YEAR(GETDATE()) * 10000 + 1231"
        )

    having = ""
    if shape == "aggregate_rollup" and rng.random() < 0.3:
        having = "\nHAVING COUNT_BIG(*) > 1"

    top_n = rng.choice((10, 25, 50, 100))
    sql = (
        f"SELECT TOP ({top_n})\n    "
        + ",\n    ".join(select_exprs)
        + f"\nFROM [fact].{db.quote_name(fact_table)} f\n"
        + "\n".join(joins)
        + "\nWHERE "
        + "\n  AND ".join(where_clauses)
        + "\nGROUP BY "
        + ", ".join(group_exprs)
        + having
        + f"\nORDER BY {order_by};"
    )
    return GeneratedQuery(shape=shape, fact_table=fact_table, sql=sql, depth=max_depth_used)


def run_procedures(ctx: BatchContext, conn) -> dict[str, int]:
    """Run every parameterless procedure in the reporting schema, in name order.

    This is the fixed half of the workload. The randomized queries differ every cycle, so
    their timings cannot be compared run to run; these always execute the same statements
    against the same windows, which makes them a usable baseline.
    """
    config = ctx.config
    schema = config.analytics_procedure_schema
    counts = {"procedures.succeeded": 0, "procedures.failed": 0}

    if not discovery.schema_exists(conn, schema):
        # An older warehouse simply has no rpt schema. Skip rather than fail the run.
        ctx.logger.warning("no_procedure_schema", procedure_schema=schema)
        return counts

    procedures = discovery.list_procedures(conn, schema)
    if not procedures:
        ctx.logger.warning("no_procedures_found", procedure_schema=schema)
        return counts

    for name in procedures:
        qualified = f"{db.quote_name(schema)}.{db.quote_name(name)}"
        started = time.perf_counter()
        try:
            cursor = conn.cursor()
            cursor.execute(f"EXEC {qualified};")
            # A procedure may return several result sets; drain them all so the timing
            # covers the whole call rather than just the first one.
            row_count = 0
            result_sets = 0
            while True:
                if cursor.description is not None:
                    row_count += len(cursor.fetchall())
                    result_sets += 1
                if not cursor.nextset():
                    break
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            counts["procedures.succeeded"] += 1
            ctx.logger.info(
                "analytics_procedure_completed",
                procedure=f"{schema}.{name}",
                duration_ms=elapsed_ms,
                row_count=row_count,
                result_sets=result_sets,
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            counts["procedures.failed"] += 1
            ctx.logger.error(
                "analytics_procedure_failed",
                procedure=f"{schema}.{name}",
                duration_ms=elapsed_ms,
                error=str(exc),
            )
    return counts


def run(ctx: BatchContext, conn) -> dict[str, int]:
    config = ctx.config
    counts: dict[str, int] = {}

    # pyodbc exposes the query timeout on the connection, not the cursor. Set it before
    # the procedures run so it covers them too.
    try:
        conn.timeout = config.analytics_query_timeout_seconds
    except Exception:  # pragma: no cover - driver without timeout support
        ctx.logger.warning("analytics_timeout_unsupported")

    if config.analytics_run_procedures:
        counts.update(run_procedures(ctx, conn))

    if not discovery.schema_exists(conn, "fact"):
        ctx.logger.warning("no_fact_schema", job_name=ctx.job_name)
        ctx.row_counts.update(counts)
        return counts
    fact_tables = discovery.list_tables(conn, "fact")
    if not fact_tables:
        ctx.logger.warning("no_fact_tables", job_name=ctx.job_name)
        ctx.row_counts.update(counts)
        return counts

    seed = config.analytics_seed if config.analytics_seed is not None else random.randrange(1_000_000)
    rng = random.Random(seed)
    cache = SchemaCache(conn)
    ctx.logger.info("analytics_started", seed=seed, query_count=config.analytics_queries_per_run)

    counts.update({"analytics.succeeded": 0, "analytics.failed": 0, "analytics.skipped": 0})
    total_ms = 0.0

    for index in range(config.analytics_queries_per_run):
        generated = build_random_query(cache, rng, fact_tables)
        if generated is None:
            counts["analytics.skipped"] += 1
            continue
        started = time.perf_counter()
        try:
            cursor = conn.cursor()
            cursor.execute(generated.sql)
            rows = cursor.fetchall()
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            total_ms += elapsed_ms
            counts["analytics.succeeded"] += 1
            ctx.logger.info(
                "analytics_query_completed",
                query_index=index,
                shape=generated.shape,
                fact_table=generated.fact_table,
                snowflake_depth=generated.depth,
                duration_ms=elapsed_ms,
                row_count=len(rows),
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            counts["analytics.failed"] += 1
            # One bad query must not abort the workload; log it with its SQL so the
            # exact statement can be replayed.
            ctx.logger.error(
                "analytics_query_failed",
                query_index=index,
                shape=generated.shape,
                fact_table=generated.fact_table,
                duration_ms=elapsed_ms,
                error=str(exc),
                sql=generated.sql,
            )

    ctx.logger.info(
        "analytics_completed",
        seed=seed,
        total_duration_ms=round(total_ms, 1),
        **{
            k.replace("analytics.", ""): v
            for k, v in counts.items()
            if k.startswith("analytics.")
        },
    )
    ctx.row_counts.update(counts)
    return counts
