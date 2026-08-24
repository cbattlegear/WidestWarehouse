from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import db, discovery
from ..etl_control import BatchContext


def run(ctx: BatchContext, conn) -> dict[str, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=ctx.config.retention_days)
    removed = 0
    landing = Path(ctx.config.landing_dir)
    if landing.exists():
        for child in landing.iterdir():
            if child.is_dir() and datetime.fromtimestamp(child.stat().st_mtime, timezone.utc) < cutoff:
                shutil.rmtree(child)
                removed += 1
    for table in ("BatchRunStep", "LoadError", "DqResult", "ScdChangeLog", "BatchRun"):
        if discovery.table_exists(conn, "etl", table):
            columns = discovery.get_column_names(conn, "etl", table)
            date_cols = [c for c in ("CreatedAtUtc", "StartedAtUtc", "CheckedAtUtc", "ErrorUtc") if c in columns]
            if date_cols:
                db.execute(
                    conn,
                    f"DELETE FROM [etl].{db.quote_name(table)} WHERE {db.quote_name(date_cols[0])} < DATEADD(day, ?, SYSUTCDATETIME())",
                    [-abs(int(ctx.config.retention_days))],
                )
    if discovery.schema_exists(conn, "fact"):
        for table in discovery.list_tables(conn, "fact"):
            db.execute(conn, f"ALTER INDEX ALL ON [fact].{db.quote_name(table)} REORGANIZE")
            db.execute(conn, f"UPDATE STATISTICS [fact].{db.quote_name(table)}")
    ctx.row_counts["landing_dirs_removed"] = removed
    return {"landing_dirs_removed": removed}
