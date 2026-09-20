"""The research module's single public surface to `app/main.py` (T78).

Same composition pattern as `app.modules.gex.router`: one `APIRouter(prefix="/research")` that
`main.py` mounts at `/api`, and the module does not find its way into the app -- the app imports
this file by name, so a broken module is an `ImportError` at boot rather than a route quietly
missing from a process that came up looking healthy.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.research.api.leaderboard import router as leaderboard_router
from app.modules.research.api.paper import router as paper_router

__all__ = ["router"]

router = APIRouter(prefix="/research")

router.include_router(leaderboard_router)
router.include_router(paper_router)
