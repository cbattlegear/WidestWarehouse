"""Tests for the fixed stored-procedure half of the analytics workload."""

from pathlib import Path

import pytest

from app.config import LoaderConfig
from app.etl_control import BatchContext
from app.jobs import analytics


class FakeLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def _record(self, level):
        def log(event, **kwargs):
            self.events.append((level, event, kwargs))

        return log

    def __getattr__(self, level):
        return self._record(level)

    def names(self) -> list[str]:
        return [event for _, event, _ in self.events]

    def kwargs_for(self, event: str) -> list[dict]:
        return [kw for _, name, kw in self.events if name == event]


class FakeCursor:
    def __init__(self, script: list[list[list[object]]], fail_on: set[str], sql_log: list[str]) -> None:
        self._script = script
        self._fail_on = fail_on
        self._sql_log = sql_log
        self._sets: list[list[list[object]]] = []
        self._index = 0

    @property
    def description(self):
        if self._index < len(self._sets):
            return [("Col",)]
        return None

    def execute(self, sql: str):
        self._sql_log.append(sql)
        for name in self._fail_on:
            if name in sql:
                raise RuntimeError(f"boom in {name}")
        self._sets = self._script.pop(0) if self._script else [[]]
        self._index = 0
        return self

    def fetchall(self):
        return self._sets[self._index]

    def nextset(self):
        self._index += 1
        return self._index < len(self._sets)


class FakeConn:
    def __init__(self, script=None, fail_on=None) -> None:
        self.script = list(script or [])
        self.fail_on = set(fail_on or ())
        self.executed: list[str] = []
        self.timeout = None

    def cursor(self):
        return FakeCursor(self.script, self.fail_on, self.executed)


def _ctx(**overrides) -> tuple[BatchContext, FakeLogger]:
    config = LoaderConfig(server="s", database="d", user="u", password="p", **overrides)
    logger = FakeLogger()
    return (
        BatchContext(
            config=config,
            batch_id=1,
            job_name="analytics",
            landing_path=Path("."),
            logger=logger,
        ),
        logger,
    )


@pytest.fixture()
def patched(monkeypatch):
    def apply(schema_present: bool, procedures: list[str]):
        monkeypatch.setattr(analytics.discovery, "schema_exists", lambda conn, schema: schema_present)
        monkeypatch.setattr(analytics.discovery, "list_procedures", lambda conn, schema: procedures)

    return apply


def test_every_procedure_runs_and_is_timed(patched) -> None:
    patched(True, ["usp_A", "usp_B"])
    ctx, logger = _ctx()
    conn = FakeConn(script=[[[["x"], ["y"]]], [[["z"]]]])

    counts = analytics.run_procedures(ctx, conn)

    assert counts == {"procedures.succeeded": 2, "procedures.failed": 0}
    assert conn.executed == ["EXEC [rpt].[usp_A];", "EXEC [rpt].[usp_B];"]
    completed = logger.kwargs_for("analytics_procedure_completed")
    assert [c["procedure"] for c in completed] == ["rpt.usp_A", "rpt.usp_B"]
    assert [c["row_count"] for c in completed] == [2, 1]
    assert all("duration_ms" in c for c in completed)


def test_all_result_sets_are_drained() -> None:
    ctx, logger = _ctx()
    conn = FakeConn(script=[[[["a"]], [["b"], ["c"]], [["d"]]]])
    cursor = conn.cursor()
    cursor.execute("EXEC [rpt].[usp_Multi];")

    # A procedure with several SELECTs must be counted in full, not just its first set.
    rows = 0
    sets = 0
    while True:
        if cursor.description is not None:
            rows += len(cursor.fetchall())
            sets += 1
        if not cursor.nextset():
            break
    assert (rows, sets) == (4, 3)


def test_one_failing_procedure_does_not_stop_the_rest(patched) -> None:
    patched(True, ["usp_Good", "usp_Bad", "usp_AlsoGood"])
    ctx, logger = _ctx()
    conn = FakeConn(script=[[[["x"]]], [[["y"]]]], fail_on={"usp_Bad"})

    counts = analytics.run_procedures(ctx, conn)

    assert counts == {"procedures.succeeded": 2, "procedures.failed": 1}
    failed = logger.kwargs_for("analytics_procedure_failed")
    assert len(failed) == 1
    assert failed[0]["procedure"] == "rpt.usp_Bad"


def test_missing_reporting_schema_warns_instead_of_failing(patched) -> None:
    # An older deployment has no rpt schema; the loader must keep running.
    patched(False, [])
    ctx, logger = _ctx()

    counts = analytics.run_procedures(ctx, FakeConn())

    assert counts == {"procedures.succeeded": 0, "procedures.failed": 0}
    assert "no_procedure_schema" in logger.names()


def test_empty_reporting_schema_warns(patched) -> None:
    patched(True, [])
    ctx, logger = _ctx()

    analytics.run_procedures(ctx, FakeConn())

    assert "no_procedures_found" in logger.names()


def test_procedure_schema_is_configurable(patched) -> None:
    patched(True, ["usp_A"])
    ctx, _ = _ctx(analytics_procedure_schema="custom")
    conn = FakeConn(script=[[[["x"]]]])

    analytics.run_procedures(ctx, conn)

    assert conn.executed == ["EXEC [custom].[usp_A];"]


def test_procedures_run_even_when_no_facts_are_deployed(monkeypatch) -> None:
    # The fixed workload must not be skipped just because the randomized half has
    # nothing to query.
    monkeypatch.setattr(analytics.discovery, "list_procedures", lambda conn, schema: ["usp_A"])
    monkeypatch.setattr(
        analytics.discovery, "schema_exists", lambda conn, schema: schema != "fact"
    )
    ctx, logger = _ctx()
    conn = FakeConn(script=[[[["x"]]]])

    counts = analytics.run(ctx, conn)

    assert counts["procedures.succeeded"] == 1
    assert "no_fact_schema" in logger.names()


def test_procedures_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setattr(analytics.discovery, "schema_exists", lambda conn, schema: schema != "fact")
    called = False

    def fail(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(analytics, "run_procedures", fail)
    ctx, _ = _ctx(analytics_run_procedures=False)

    analytics.run(ctx, FakeConn())

    assert called is False
