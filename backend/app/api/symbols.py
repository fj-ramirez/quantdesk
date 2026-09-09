"""`GET /api/symbols` -- the core/extended universe split, at the wire (T47).

A reader of `/api/snapshots` (or any other per-underlying endpoint) cannot tell a core
underlying (`settings.symbols`, captured at 16:20 ET, the P0 guardrail) from an extended one
(`settings.extended_symbols`, captured separately at 16:45 ET) just by looking at a symbol
string -- both are just `Underlying` values once captured. This endpoint is the one place that
distinction is made explicit, so the frontend's symbol switcher (`TopBar`) can group them
instead of presenting one flat, unlabelled list that silently implies every symbol has the same
capture guarantees.

Deliberately tiny: `Settings.symbols` / `Settings.extended_symbols` are already parsed,
validated-at-startup lists (see `app/config.py`), so this route does no work beyond echoing
them -- no DB read, no filesystem, nothing that could fail or go stale.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

__all__ = ["router"]

router = APIRouter(prefix="/symbols", tags=["symbols"])


class SymbolsResponse(BaseModel):
    #: The five symbols the 16:20 EOD job (and its 20:00 safety net) captures. Never widened by
    #: this endpoint or by anything reading it -- see `app/config.py`'s `SYMBOLS` docstring.
    core: list[str]
    #: T47's sector/industry ETFs, captured by the separate 16:45 ET job. A symbol appearing
    #: here carries no guarantee about the 16:20 job's timing or reliability.
    extended: list[str]


@router.get("", response_model=SymbolsResponse)
def get_symbols() -> SymbolsResponse:
    return SymbolsResponse(core=settings.symbols, extended=settings.extended_symbols)
