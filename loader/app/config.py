from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


class ConfigError(ValueError):
    pass


def _bool(value: str | bool | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if value.lower() in {"1", "true", "yes", "y", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"Invalid boolean value: {value!r}")


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 1) -> int:
    raw = env.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _optional_int(env: Mapping[str, str], name: str) -> int | None:
    raw = env.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class LoaderConfig:
    server: str
    database: str
    user: str
    password: str
    encrypt: str = "yes"
    trust_server_certificate: str = "yes"
    driver: str = "ODBC Driver 18 for SQL Server"
    pipeline_cron: str = "*/15 * * * *"
    dq_cron: str = "0 * * * *"
    housekeeping_cron: str = "0 2 * * *"
    analytics_cron: str = "*/5 * * * *"
    analytics_queries_per_run: int = 10
    analytics_query_timeout_seconds: int = 60
    analytics_seed: int | None = None
    batch_rows_per_cycle: int = 1000
    landing_dir: Path = Path("/data/landing")
    run_on_startup: bool = True
    log_level: str = "INFO"
    retention_days: int = 14
    connection_timeout_seconds: int = 10

    @classmethod
    def from_env(
        cls,
        env_file: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "LoaderConfig":
        if env_file:
            load_dotenv(env_file)
        elif env is None:
            load_dotenv()
        source = dict(os.environ if env is None else env)
        missing = [
            name
            for name in ("DW_SERVER", "DW_DATABASE", "DW_USER", "DW_PASSWORD")
            if not source.get(name)
        ]
        if missing:
            raise ConfigError("Missing required environment variable(s): " + ", ".join(missing))
        return cls(
            server=source["DW_SERVER"],
            database=source["DW_DATABASE"],
            user=source["DW_USER"],
            password=source["DW_PASSWORD"],
            encrypt=source.get("DW_ENCRYPT", "yes"),
            trust_server_certificate=source.get("DW_TRUST_SERVER_CERTIFICATE", "yes"),
            driver=source.get("DW_DRIVER", "ODBC Driver 18 for SQL Server"),
            pipeline_cron=source.get("PIPELINE_CRON", source.get("BATCH_PIPELINE_CRON", "*/15 * * * *")),
            dq_cron=source.get("DQ_CRON", "0 * * * *"),
            housekeeping_cron=source.get("HOUSEKEEPING_CRON", "0 2 * * *"),
            analytics_cron=source.get("ANALYTICS_CRON", "*/5 * * * *"),
            analytics_queries_per_run=_int(source, "ANALYTICS_QUERIES_PER_RUN", 10),
            analytics_query_timeout_seconds=_int(source, "ANALYTICS_QUERY_TIMEOUT_SECONDS", 60),
            analytics_seed=_optional_int(source, "ANALYTICS_SEED"),
            batch_rows_per_cycle=_int(source, "BATCH_ROWS_PER_CYCLE", 1000),
            landing_dir=Path(source.get("LANDING_DIR", "/data/landing")),
            run_on_startup=_bool(source.get("RUN_ON_STARTUP"), True),
            log_level=source.get("LOG_LEVEL", "INFO").upper(),
            retention_days=_int(source, "RETENTION_DAYS", 14),
            connection_timeout_seconds=_int(source, "CONNECTION_TIMEOUT_SECONDS", 10),
        )

    def odbc_connection_string(self) -> str:
        parts = {
            "DRIVER": "{" + self.driver + "}",
            "SERVER": self.server,
            "DATABASE": self.database,
            "UID": self.user,
            "PWD": self.password,
            "Encrypt": self.encrypt,
            "TrustServerCertificate": self.trust_server_certificate,
            "Connection Timeout": str(self.connection_timeout_seconds),
        }
        return ";".join(f"{k}={v}" for k, v in parts.items()) + ";"
