"""The broker module's single public surface to `app/main.py` (T159).

Same composition pattern as the other modules: one `APIRouter(prefix="/broker")` that `main.py`
mounts at `/api`, imported by name. Every route under it reads; the order path is the
`executor` worker's alone (invariant 11), and nothing here imports it.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.broker.api.chart import router as chart_router

__all__ = ["router"]

router = APIRouter(prefix="/broker")

router.include_router(chart_router)
