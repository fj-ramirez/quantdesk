import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.db import get_session_factory
from app.core.version import all_service_versions
from app.modules.gex.router import router as gex_module_router
from app.modules.research.router import router as research_module_router
from app.modules.terminal.router import router as terminal_module_router

# The structured JSON capture logging in `app/modules/gex/jobs/capture.py` (T05: "every
# capture logged -- symbol, contract count, spot, duration, error") is only useful if it
# actually reaches stdout. uvicorn configures its own `uvicorn.*` loggers but leaves the root
# logger (and therefore this app's own loggers, which propagate to it) unconfigured, so
# without this a capture failure at 16:20 would be logged and then silently go nowhere.
# `basicConfig` is a no-op if something else already attached a handler to the root logger, so
# this is safe to call unconditionally at import time.
#
# T75 note: the scheduled captures now run in `app/workers/gex_capture.py`, which calls this
# for itself. It stays here because the manual `POST /api/gex/snapshots/capture` route still
# runs a capture in *this* process and logs the same way.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

logger = logging.getLogger("app.main")

# **No lifespan, deliberately, and this is the load-bearing change in T75.**
#
# Until T75 this app's lifespan started APScheduler and fired the startup catch-up task. With
# three modules sharing one API process that is wrong twice over: a `--reload` dev restart
# re-runs the catch-up, and an API-only redeploy interrupts capture. Both moved to
# `app/workers/gex_capture.py`, which has its own container.
#
# The parameter is *absent* rather than an empty function on purpose. An empty lifespan is an
# invitation to add "just one small thing" to it, and the property this initiative needs is
# categorical: the API process starts no background work at all. That is what lets research
# (T77) and terminal (T79) add their own workers without this file growing a third scheduler.
#
# If you are here because something needs to run on a clock, it belongs in `app/workers/`.
app = FastAPI(title="quantdesk API")

# T11: the Vite dev server (frontend/, T12+) runs on 5173 and calls this API cross-origin.
# This is a single-user, analysis-only app with no cookies/auth to leak, so a narrow allowlist
# of the one real dev origin is simpler than wildcarding and just as safe.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# One line per module, imported by name -- see `app/modules/__init__.py` for why there is no
# discovery mechanism. `research` (T77) and `terminal` (T79) join this list.
app.include_router(gex_module_router, prefix="/api")
# T78. The second module's routes. Nothing else in `main.py` changed to add it, which is
# the whole point of the T75 split: a module is a router and a worker, not a special case.
app.include_router(research_module_router, prefix="/api")
app.include_router(terminal_module_router, prefix="/api")


@app.get("/health")
async def health() -> dict:
    """Liveness + database reachability, for humans and for the container healthcheck.

    **Stays at the root, unprefixed**, even though every module's routes moved under
    `/api/<module>` in T75. `compose.prod.yaml`'s backend healthcheck hits
    `http://127.0.0.1:8001/health` and requires `db == "ok"`, so this path is a deployment
    contract rather than an API route. Per-module health lives at `/api/<module>/health`.

    Deliberately kept cheap. `GET /api/gex/health/capture` answers the *dataset* question ("is
    my data still whole?") and costs a query per symbol across the ~125-symbol scan universe --
    fine for a human or a monitor on a slow interval, far too heavy to run every 30 seconds as
    a Docker healthcheck. This route issues one `SELECT 1` instead.

    The `db` field is additive: the pre-existing `status`/`provider`/`symbols` keys are
    unchanged, so nothing that already reads this endpoint has to care. `status` stays "ok"
    whenever the process is serving -- a failed probe is reported in `db`, not by flipping
    `status`, so "the API is up but Postgres is not" stays distinguishable from "the API is
    down" (which is a connection error, not a response at all).

    `services` is additive in the same way, and answers "which build is each container
    running" -- **per service, never one number for the stack**. On 2026-09-21 a
    `docker compose up -d --build` updated four containers and failed on the fifth, and
    nothing running said so. Each entry carries a `label` in the `service: time (sha)` form
    plus the same facts as fields; the API reports itself from its environment and the
    workers from the files they write at boot (`app.core.version`). A worker that has not
    booted since the feature landed is simply absent, which is the honest rendering of "it
    has not said".

    Reading four small files per probe is deliberate and cheap -- and it is why this stays
    on the 30-second healthcheck route rather than becoming a query.
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
        "services": [v.to_dict() for v in all_service_versions()],
    }
