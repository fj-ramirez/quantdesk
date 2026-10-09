"""Container entrypoint for the `executor` worker (T151, plans/charter-mt5/README.md step 4).

The only process on the desk that places orders. It runs `Executor.tick` every 20 seconds:
schedule the enabled strategies' legs, then send whatever is due. See
`app/modules/broker/executor.py` for the rules. It needs the broker tables (Alembic) and the
`mt5` bridge, and does nothing beyond journalling until `EXECUTOR_ENABLED=true`.

Structurally it is `broker_ingest.py`: build, start, run until a signal, shut down.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core import notify
from app.core.config import settings
from app.core.db import get_engine, get_session_factory
from app.core.version import record_service_version
from app.modules.broker.client import BridgeClient
from app.modules.broker.executor import Executor
from app.modules.broker.strategies import STRATEGIES
from app.modules.broker.tables import schema_ready

logger = logging.getLogger("app.workers.executor")


def _strategies() -> list:
    names = [n.strip() for n in settings.EXECUTOR_STRATEGIES.split(",") if n.strip()]
    unknown = [n for n in names if n not in STRATEGIES]
    if unknown:
        raise SystemExit(f"executor: unknown strategies {unknown}; known: {sorted(STRATEGIES)}")
    return [STRATEGIES[n] for n in names]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    record_service_version("executor")

    addr = settings.BROKER_BRIDGE_ADDR.strip()
    if not addr:
        logger.error("executor: BROKER_BRIDGE_ADDR is empty; nothing to trade through")
        return 1
    for _ in range(60):
        try:
            if schema_ready(get_engine()):
                break
        except Exception as exc:  # noqa: BLE001 - postgres may not be up yet
            logger.info("executor: database not ready (%s)", type(exc).__name__)
        await asyncio.sleep(5.0)
    else:
        logger.error("executor: broker tables missing; has the backend run its migrations?")
        return 1

    host, _, port = addr.rpartition(":")
    client = BridgeClient(host, int(port))
    executor = Executor(
        client, get_session_factory(), _strategies(), enabled=settings.EXECUTOR_ENABLED,
        volume=settings.EXECUTOR_VOLUME, max_volume=settings.EXECUTOR_MAX_VOLUME,
        notify=lambda msg: notify.send(msg, level=logging.WARNING),
    )
    try:
        acc = await client.account()
        logger.info("executor: #%s on %s (%s), trade_allowed=%s, enabled=%s, strategies=%s",
                    acc.login, acc.server, acc.trade_mode, acc.trade_allowed,
                    settings.EXECUTOR_ENABLED, [s.name for s in executor.strategies])
        await executor.reconcile(_now())
    except Exception as exc:  # noqa: BLE001 - the bridge may still be starting; ticks retry
        logger.warning("executor: bridge not answering yet: %s", exc)

    async def run() -> None:
        try:
            await executor.tick(_now())
        except Exception:
            logger.exception("executor: tick failed")

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(run, IntervalTrigger(seconds=20), id="executor_tick", max_instances=1,
                      coalesce=True, misfire_grace_time=15)
    scheduler.start()

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
    logger.info("executor: stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
