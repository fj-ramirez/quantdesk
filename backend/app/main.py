import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.bars import router as bars_router
from app.api.chains import router as chains_router
from app.api.decisions import router as decisions_router
from app.api.gex import router as gex_router
from app.api.health import router as health_router
from app.api.report import router as report_router
from app.api.scan import router as scan_router
from app.api.snapshots import router as snapshots_router
from app.api.stream import router as stream_router
from app.api.symbols import router as symbols_router
from app.config import settings
from app.jobs.capture import get_session_factory
from app.jobs.catchup import startup_catchup_job
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

    # T29: catch up a missed 16:20 EOD capture (laptop was off, container was down, whatever)
    # without making boot wait on it. `create_task` schedules `startup_catchup_job` to run on
    # this same event loop as soon as it gets a turn -- typically right after this generator
    # yields and the app starts accepting requests -- rather than being awaited here, which
    # would stall every request behind up to three sequential Cboe fetches. The job function
    # itself never raises (see its own docstring), so there is nothing here to catch.
    app.state.startup_catchup_task = asyncio.create_task(startup_catchup_job())
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        catchup_task = app.state.startup_catchup_task
        if not catchup_task.done():
            catchup_task.cancel()


app = FastAPI(title="GEX Trading API", lifespan=lifespan)

# T11: the Vite dev server (frontend/, T12+) runs on 5173 and calls this API cross-origin.
# This is a single-user, analysis-only app with no cookies/auth to leak, so a narrow allowlist
# of the one real dev origin is simpler than wildcarding and just as safe.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(snapshots_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(gex_router, prefix="/api")
app.include_router(chains_router, prefix="/api")
app.include_router(report_router, prefix="/api")
app.include_router(bars_router, prefix="/api")
app.include_router(scan_router, prefix="/api")
app.include_router(symbols_router, prefix="/api")
app.include_router(decisions_router, prefix="/api")
app.include_router(stream_router, prefix="/api")


@app.get("/health")
async def health() -> dict:
    """Liveness + database reachability, for humans and for the container healthcheck.

    Deliberately kept cheap. `GET /api/health/capture` answers the *dataset* question ("is my
    data still whole?") and costs a query per symbol across the ~125-symbol scan universe --
    fine for a human or a monitor on a slow interval, far too heavy to run every 30 seconds as
    a Docker healthcheck. This route issues one `SELECT 1` instead.

    The `db` field is additive: the pre-existing `status`/`provider`/`symbols` keys are
    unchanged, so nothing that already reads this endpoint has to care. `status` stays "ok"
    whenever the process is serving -- a failed probe is reported in `db`, not by flipping
    `status`, so "the API is up but Postgres is not" stays distinguishable from "the API is
    down" (which is a connection error, not a response at all).
    """
    db = "ok"
    try:
        with get_session_factory()() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        # Never surface the exception text: `DATABASE_URL` carries the password and SQLAlchemy
        # puts the URL in its connection-error messages. The log line below is where a human
        # goes for the detail; this endpoint is reachable from the proxy.
        logger.exception("health: database probe failed")
        db = "error"
    return {
        "status": "ok",
        "provider": settings.PROVIDER,
        "symbols": settings.symbols,
        "db": db,
    }
