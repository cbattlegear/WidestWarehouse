from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Sequence

import pandas as pd
import pyodbc
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import LoaderConfig

TRANSIENT_SQL_STATES = {
    "08S01",
    "40001",
    "HYT00",
    "HYT01",
    "4060",
    "40197",
    "40501",
    "40613",
    "49918",
    "49919",
    "49920",
}


def is_transient_error(exc: BaseException) -> bool:
    text = " ".join(str(arg) for arg in getattr(exc, "args", ())) or str(exc)
    return isinstance(exc, pyodbc.Error) and any(state in text for state in TRANSIENT_SQL_STATES)


@retry(
    retry=retry_if_exception(is_transient_error),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def connect(config: LoaderConfig):
    return pyodbc.connect(config.odbc_connection_string(), autocommit=False)


@contextmanager
def transaction(config: LoaderConfig):
    conn = connect(config)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@retry(
    retry=retry_if_exception(is_transient_error),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    stop=stop_after_attempt(4),
    reraise=True,
)
def execute(conn, sql: str, params: Sequence[Any] | None = None) -> int:
    cur = conn.cursor()
    cur.execute(sql, params or [])
    return cur.rowcount if cur.rowcount is not None else -1


@retry(
    retry=retry_if_exception(is_transient_error),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    stop=stop_after_attempt(4),
    reraise=True,
)
def executemany(conn, sql: str, rows: Iterable[Sequence[Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    cur = conn.cursor()
    cur.fast_executemany = True
    cur.executemany(sql, rows)
    return len(rows)


def query(conn, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(sql, params or [])
    columns = [col[0] for col in (cur.description or [])]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def quote_name(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def bulk_insert_dataframe(conn, schema: str, table: str, df: pd.DataFrame, chunk_size: int = 1000) -> int:
    if df.empty:
        return 0
    clean = df.where(pd.notnull(df), None)
    cols = list(clean.columns)
    col_sql = ", ".join(quote_name(c) for c in cols)
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {quote_name(schema)}.{quote_name(table)} ({col_sql}) VALUES ({placeholders})"
    total = 0
    for start in range(0, len(clean), chunk_size):
        rows = [tuple(row) for row in clean.iloc[start : start + chunk_size].itertuples(index=False, name=None)]
        total += executemany(conn, sql, rows)
    return total
