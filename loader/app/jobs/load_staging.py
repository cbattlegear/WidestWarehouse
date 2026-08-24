from __future__ import annotations

from pathlib import Path

import pandas as pd

from .. import db, discovery
from ..etl_control import BatchContext


def run(ctx: BatchContext, conn) -> dict[str, int]:
    batch_dir = Path(ctx.config.landing_dir) / str(ctx.batch_id)
    counts: dict[str, int] = {}
    if not batch_dir.exists():
        ctx.logger.warning("landing_batch_missing", batch_id=ctx.batch_id, path=str(batch_dir))
        return counts
    for csv_file in sorted(batch_dir.glob("stg.*.csv")):
        table = csv_file.name[len("stg.") : -len(".csv")]
        if not discovery.table_exists(conn, "stg", table):
            ctx.logger.warning("staging_table_missing", table=table)
            continue
        db.execute(conn, f"TRUNCATE TABLE [stg].{db.quote_name(table)}")
        try:
            loaded = db.bulk_insert_dataframe(conn, "stg", table, pd.read_csv(csv_file))
        except Exception as exc:
            raise RuntimeError(f"staging load failed for stg.{table}: {exc}") from exc
        counts[f"stg.{table}"] = loaded
    ctx.row_counts.update(counts)
    return counts
