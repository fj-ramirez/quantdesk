import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.snapshots import router as snapshots_router
from app.config import settings
from app.jobs.scheduler import build_scheduler

# The structured JSON capture logging in `app/jobs/capture.py` (T05: "every capture logged --
# symbol, contract count, spot, duration, error") is only useful if it actually reaches
# stdout. uvicorn configures its own `uvicorn.*` loggers but leaves the root logger (and
# therefore this app's own loggers, which propagate to it) unconfigured, so without this a
# capture failure at 16:20 would be logged and then silently go nowhere. `basicConfig` is a
# no-op if something else already attached a handler to the root logger, so this is safe to
# call unconditionally at import time.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the EOD capture scheduler for the life of the process (T05).

    Building the scheduler is separated from starting it (`app.jobs.scheduler.build_scheduler`)
    so tests can construct one and inspect its registered job/trigger without ever starting it
    -- this lifespan is the only place `.start()` is called for the running app.
    """
    scheduler = build_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("scheduler started; jobs=%s", [job.id for job in scheduler.get_jobs()])
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="GEX Trading API", lifespan=lifespan)
app.include_router(snapshots_router, prefix="/api")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "provider": settings.PROVIDER, "symbols": settings.symbols}
