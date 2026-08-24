from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from app.config import LoaderConfig
from app.etl_control import MissingWarehouseSchema, unique_batch_id
from app.main import build_scheduler, run_pipeline


def cfg(tmp_path: Path) -> LoaderConfig:
    return LoaderConfig.from_env(
        env={
            "DW_SERVER": "server",
            "DW_DATABASE": "db",
            "DW_USER": "user",
            "DW_PASSWORD": "pw",
            "LANDING_DIR": str(tmp_path),
        }
    )


def test_batch_ids_are_unique(monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter([1000.001, 1000.002])
    monkeypatch.setattr("app.etl_control.time.time", lambda: next(values))
    assert unique_batch_id() != unique_batch_id()


def test_scheduler_wiring(tmp_path: Path) -> None:
    scheduler = build_scheduler(cfg(tmp_path))
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"pipeline", "data_quality", "housekeeping"}
    assert all(job.max_instances == 1 and job.coalesce for job in jobs.values())


def test_missing_schema_is_logged_and_not_raised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    @contextmanager
    def fake_transaction(_config):
        yield object()

    @contextmanager
    def fake_lock(_conn, _name):
        yield

    monkeypatch.setattr("app.main.db.transaction", fake_transaction)
    monkeypatch.setattr("app.main.process_lock", fake_lock)
    monkeypatch.setattr("app.main.open_batch_run", lambda _conn, _job: (_ for _ in ()).throw(MissingWarehouseSchema("missing etl.BatchRun")))
    run_pipeline(cfg(tmp_path))
