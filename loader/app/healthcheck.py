from __future__ import annotations

import sys

from .config import ConfigError, LoaderConfig
from .db import connect, query


def main() -> int:
    try:
        config = LoaderConfig.from_env()
        conn = connect(config)
        try:
            rows = query(conn, "SELECT 1 AS ok")
            return 0 if rows and rows[0]["ok"] == 1 else 2
        finally:
            conn.close()
    except (ConfigError, Exception) as exc:
        print(f"healthcheck failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
