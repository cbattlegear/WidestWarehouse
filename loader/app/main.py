from __future__ import annotations

import signal
import time
from pathlib import Path

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db
from .config import ConfigError, LoaderConfig
from .etl_control import (
    BatchContext,
    MissingWarehouseSchema,
    close_batch_run,
    log_error,
    open_batch_run,
    process_lock,
    record_step,
)
from .jobs import analytics, data_quality, generate_batch, housekeeping, load_facts, load_staging, merge_dimensions
from .logging_config import configure_logging

MISFIRE_GRACE_SECONDS = 3600


def cron_trigger(expr: str) -> CronTrigger:
    parts = expr.split()
    if len(parts) != 5:
        raise ConfigError(f"Cron expression must have five fields: {expr}")
    minute, hour, day, month, day_of_week = parts
    return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week)


def run_pipeline(config: LoaderConfig) -> None:
    logger = structlog.get_logger("loader.pipeline")
    started = time.perf_counter()
    batch_id = -1
    try:
        with db.transaction(config) as conn:
            with process_lock(conn, "WidestWarehouse.loader.pipeline"):
                batch_id = open_batch_run(conn, "pipeline")
                ctx = BatchContext(
                    config,
                    batch_id,
                    "pipeline",
                    Path(config.landing_dir) / str(batch_id),
                    logger.bind(batch_id=batch_id, job_name="pipeline"),
                )
                for name, job in (
                    ("generate_batch", generate_batch.run),
                    ("load_staging", load_staging.run),
                    ("merge_dimensions", merge_dimensions.run),
                    ("load_facts", load_facts.run),
                ):
                    step_start = time.perf_counter()
                    try:
                        counts = job(ctx, conn)
                        record_step(conn, batch_id, name, "Succeeded", sum(counts.values()) if counts else 0)
                        logger.info(
                            "job_completed",
                            batch_id=batch_id,
                            job_name=name,
                            duration_seconds=round(time.perf_counter() - step_start, 3),
                            row_counts=counts,
                        )
                    except Exception as exc:
                        record_step(conn, batch_id, name, "Failed", 0, str(exc))
                        log_error(conn, batch_id, name, exc)
                        raise RuntimeError(f"job '{name}' failed: {exc}") from exc
                close_batch_run(conn, batch_id, "Succeeded", ctx.row_counts)
        logger.info("pipeline_completed", batch_id=batch_id, duration_seconds=round(time.perf_counter() - started, 3))
    except MissingWarehouseSchema as exc:
        logger.error("warehouse_schema_missing", error=str(exc))
    except Exception as exc:
        logger.exception("pipeline_failed", batch_id=batch_id, error=str(exc))


def run_single(config: LoaderConfig, job_name: str, job) -> None:
    logger = structlog.get_logger(f"loader.{job_name}")
    started = time.perf_counter()
    batch_id = -1
    try:
        with db.transaction(config) as conn:
            with process_lock(conn, f"WidestWarehouse.loader.{job_name}"):
                batch_id = open_batch_run(conn, job_name)
                ctx = BatchContext(
                    config,
                    batch_id,
                    job_name,
                    Path(config.landing_dir) / str(batch_id),
                    logger.bind(batch_id=batch_id, job_name=job_name),
                )
                counts = job(ctx, conn)
                close_batch_run(conn, batch_id, "Succeeded", ctx.row_counts)
                logger.info(
                    "job_completed",
                    batch_id=batch_id,
                    job_name=job_name,
                    duration_seconds=round(time.perf_counter() - started, 3),
                    row_counts=counts,
                )
    except MissingWarehouseSchema as exc:
        logger.error("warehouse_schema_missing", job_name=job_name, error=str(exc))
    except Exception as exc:
        logger.exception("job_failed", batch_id=batch_id, job_name=job_name, error=str(exc))


def run_analytics(config: LoaderConfig) -> None:
    """Read-only workload. It deliberately skips the batch-run bookkeeping and the
    application lock so a long analytical query can never block the load pipeline."""
    logger = structlog.get_logger("loader.analytics")
    started = time.perf_counter()
    conn = None
    try:
        conn = db.connect(config)
        ctx = BatchContext(
            config,
            -1,
            "analytics",
            Path(config.landing_dir),
            logger.bind(job_name="analytics"),
        )
        analytics.run(ctx, conn)
        # Nothing was written, but SELECTs still open a transaction under autocommit=False.
        conn.rollback()
        logger.info("analytics_finished", duration_seconds=round(time.perf_counter() - started, 3))
    except MissingWarehouseSchema as exc:
        logger.error("warehouse_schema_missing", job_name="analytics", error=str(exc))
    except Exception as exc:
        logger.exception("analytics_failed", error=str(exc))
    finally:
        if conn is not None:
            conn.close()


def build_scheduler(config: LoaderConfig) -> BlockingScheduler:
    # A cycle can outlast its trigger interval; without grace APScheduler silently skips runs.
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_pipeline,
        cron_trigger(config.pipeline_cron),
        args=[config],
        id="pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        replace_existing=True,
    )
    scheduler.add_job(
        run_single,
        cron_trigger(config.dq_cron),
        args=[config, "data_quality", data_quality.run],
        id="data_quality",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        replace_existing=True,
    )
    scheduler.add_job(
        run_single,
        cron_trigger(config.housekeeping_cron),
        args=[config, "housekeeping", housekeeping.run],
        id="housekeeping",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        replace_existing=True,
    )
    scheduler.add_job(
        run_analytics,
        cron_trigger(config.analytics_cron),
        args=[config],
        id="analytics",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        replace_existing=True,
    )
    return scheduler


def main() -> int:
    try:
        config = LoaderConfig.from_env()
        configure_logging(config.log_level)
        Path(config.landing_dir).mkdir(parents=True, exist_ok=True)
        scheduler = build_scheduler(config)
        log = structlog.get_logger("loader.main")

        def stop(signum, _frame):
            log.info("shutdown_signal", signal=signum)
            scheduler.shutdown(wait=False)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        if config.run_on_startup:
            run_pipeline(config)
        log.info("scheduler_starting", jobs=[job.id for job in scheduler.get_jobs()])
        scheduler.start()
        return 0
    except (KeyboardInterrupt, SystemExit):
        return 0
    except ConfigError as exc:
        print(f"configuration error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
