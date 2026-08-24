from __future__ import annotations

import pytest

from app.config import ConfigError, LoaderConfig


def test_config_requires_connection_values() -> None:
    with pytest.raises(ConfigError, match="DW_SERVER"):
        LoaderConfig.from_env(env={})


def test_connection_string_uses_driver_and_security_options() -> None:
    cfg = LoaderConfig.from_env(
        env={
            "DW_SERVER": "host.docker.internal,1433",
            "DW_DATABASE": "WidestWarehouse",
            "DW_USER": "loader",
            "DW_PASSWORD": "secret",
            "DW_ENCRYPT": "yes",
            "DW_TRUST_SERVER_CERTIFICATE": "yes",
        }
    )
    conn = cfg.odbc_connection_string()
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in conn
    assert "SERVER=host.docker.internal,1433" in conn
    assert "DATABASE=WidestWarehouse" in conn
    assert "Encrypt=yes" in conn
    assert "TrustServerCertificate=yes" in conn
