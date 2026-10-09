"""Container entrypoint for the `broker-ingest` worker (T144, plans/charter-mt5/README.md).

Reads the `mt5` container's bridge and writes `broker.` once a minute (clock check and M1 bars)
and once an hour (specs and ticks). See `app/modules/broker/ingest.py` for the rules. The tables
come from Alembic (revision `f7a8b9c0d1e2`); this worker waits for them and creates nothing.

Structurally it is `capture_watch.py`: build, start, run until a signal, shut down without
waiting.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.db import get_engine, get_session_factory
from app.core.version import record_service_version
from app.modules.broker.client import BridgeClient
from app.modules.broker.ingest import Ingestor
from app.modules.broker.tables import schema_ready

logger = logging.getLogger("app.workers.broker_ingest")


def _symbols() -> list[str]:
    return [s.strip() for s in settings.BROKER_SYMBOLS.split(",") if s.strip()]


async def _wait_for_schema(*, timeout_seconds: float = 300.0) -> bool:
    """Wait for the backend's `alembic upgrade head` to have created the broker tables."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            if schema_ready(get_engine()):
                return True
            logger.info("broker_ingest: broker tables not there yet; waiting for migrations")
        except Exception as exc:  # noqa: BLE001 - postgres may simply not be up yet
            logger.info("broker_ingest: database not ready (%s)", type(exc).__name__)
        await asyncio.sleep(5.0)
    return False


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    record_service_version("broker-ingest")

    addr = settings.BROKER_BRIDGE_ADDR.strip()
    if not addr:
        logger.error("broker_ingest: BROKER_BRIDGE_ADDR is empty; nothing to ingest from")
        return 1
    host, _, port = addr.rpartition(":")
    if not await _wait_for_schema():
        logger.error("broker_ingest: broker tables missing; has the backend run its migrations?")
        return 1

    client = BridgeClient(host, int(port))
    try:
        account = await client.account()
        logger.info("broker_ingest: #%s on %s (%s), symbols %s", account.login, account.server,
                    account.trade_mode, _symbols())
    except Exception as exc:  # noqa: BLE001 - the bridge may still be starting; jobs retry
        logger.warning("broker_ingest: bridge not answering yet: %s", exc)

    ingestor = Ingestor(client=client, factory=get_session_factory(), symbols=_symbols(),
                        data_dir=Path(settings.DATA_DIR), tick_hours=settings.BROKER_TICK_HOURS)
    # misfire_grace_time: APScheduler's default is one second. The history walk's large
    # synchronous upserts hold the event loop for longer than that, and on 2026-10-08 the 01:02
    # hourly run was dropped as "missed by 0:00:06", so no tick files were written that hour.
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(ingestor.run_minute, IntervalTrigger(minutes=1), id="broker_minute",
                      max_instances=1, coalesce=True, misfire_grace_time=55)
    scheduler.add_job(ingestor.run_hour, CronTrigger(minute=2), id="broker_hour",
                      max_instances=1, coalesce=True, misfire_grace_time=1800)
    scheduler.start()
    logger.info("broker_ingest: scheduler started; jobs=%s", [j.id for j in scheduler.get_jobs()])
    try:
        await ingestor.run_hour()
    except Exception:  # the boot run is a convenience; the scheduled one retries
        logger.exception("broker_ingest: boot run of the hourly job failed")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass
    await stop.wait()

    scheduler.shutdown(wait=False)
    await client.close()
    logger.info("broker_ingest: stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
