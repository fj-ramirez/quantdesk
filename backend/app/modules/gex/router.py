"""The GEX module's single public surface to `app/main.py` (T75).

One `APIRouter(prefix="/gex")` composing the ten routers that used to be included directly at
`/api`. `main.py` mounts this at `/api`, so every route the app served before T75 is now
reachable at the same path with one extra segment: `/api/snapshots` became
`/api/gex/snapshots`, and `/api/health/capture` became `/api/gex/health/capture`.

**Why reprefix at all, rather than leaving GEX at the root of `/api`.** Symmetry now is
cheaper than a migration later. Keeping one module privileged at `/api` would make research
and terminal look like bolt-ons, and the two would collide the moment both wanted
`/api/health` -- which they will, since per-module capture/ingest freshness is the first thing
each of them needs to report.

**Composition, not self-registration.** This module does not find its way into the app; the
app imports this file by name. A missing or broken module is then an `ImportError` at boot,
loudly, rather than a route that is quietly absent from a process that came up looking fine.

One wart, recorded rather than fixed: `api/gex.py`'s own router already carries `prefix="/gex"`
(it is the levels/profile API, named before there was a module called gex), so its routes read
`/api/gex/gex/{underlying}/latest`. T75's brief is explicit that no GEX behaviour may be
renamed or re-specified, and renaming that router is a rename. It costs one line here and one
in the frontend client whenever a later task decides to clean it up.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.gex.api.bars import router as bars_router
from app.modules.gex.api.chains import router as chains_router
from app.modules.gex.api.decisions import router as decisions_router
from app.modules.gex.api.gex import router as gex_router
from app.modules.gex.api.health import router as health_router
from app.modules.gex.api.report import router as report_router
from app.modules.gex.api.scan import router as scan_router
from app.modules.gex.api.snapshots import router as snapshots_router
from app.modules.gex.api.stream import router as stream_router
from app.modules.gex.api.symbols import router as symbols_router

__all__ = ["router"]

router = APIRouter(prefix="/gex")

# Same ten, in the same order `main.py` included them before T75, so a diff of the route table
# is a diff of the prefix and nothing else.
router.include_router(snapshots_router)
router.include_router(health_router)
router.include_router(gex_router)
router.include_router(chains_router)
router.include_router(report_router)
router.include_router(bars_router)
router.include_router(scan_router)
router.include_router(symbols_router)
router.include_router(decisions_router)
router.include_router(stream_router)
