"""The terminal module's single public surface to `app/main.py` (T80).

Same composition pattern as the other two modules: one `APIRouter(prefix="/terminal")` that
`main.py` mounts at `/api`, imported by name so a broken module is an `ImportError` at boot
rather than a route quietly missing from a process that came up looking healthy.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.terminal.api.board import router as board_router
from app.modules.terminal.api.edges import router as edges_router

__all__ = ["router"]

router = APIRouter(prefix="/terminal")

router.include_router(board_router)
router.include_router(edges_router)
