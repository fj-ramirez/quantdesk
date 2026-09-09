"""Normalized daily-bar schema (T42, plans/continuation/00-foundation-daily-bars.md).

Mirrors the design of `app.models.chain`: a small, frozen Pydantic model sits at the trust
boundary where untrusted vendor JSON becomes application data, so a provider mapping bug is an
immediate, located `ValidationError` rather than a silently wrong row landing in Postgres.

**`open`/`high`/`low`/`close` are required, non-null floats.** Yahoo's ``indicators.quote[0]``
carries parallel arrays that can hold `null` in the same position across `open`/`high`/`low`/
`close` on a halted or untraded day (see the Yahoo provider's docstring) -- a provider must
skip that row entirely rather than construct a `DailyBar` with a fabricated `0.0`, so this
model simply has no `None` case for price fields to accidentally accept.

**`volume` is nullable, and `0` is a real, distinct value from `None`** -- the same
None-means-unknown / 0-means-zero discipline CLAUDE.md's invariant 3 applies to open interest.
`^VIX` genuinely trades zero contract volume (it is a calculated index, not a listed
instrument); mapping that `0` to `None` would misreport "no data" as what is actually a
correct, if boring, measurement. A future provider that has no volume field at all supplies
`None` instead.

**`date` is a `datetime.date`, not a `datetime`.** A daily bar has no instant -- it summarizes
a whole session -- so giving it a `datetime` would invite someone to read a time-of-day out of
it that was never real. Providers derive `date` from the vendor's session-open timestamp
converted into the *exchange's own timezone* (`meta.exchangeTimezoneName` for Yahoo), not UTC:
see the Yahoo provider module docstring for why a UTC-based conversion silently breaks for any
row stamped after 19:00 ET.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["DailyBar"]


class DailyBar(BaseModel):
    """One symbol's OHLCV summary for one exchange-local trading date."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, description="Plain ticker as this app knows it (SPY, ^VIX, SPX).")
    date: dt.date = Field(description="Exchange-local trading date the bar summarizes.")
    open: float = Field(gt=0, description="Session open.")
    high: float = Field(gt=0, description="Session high.")
    low: float = Field(gt=0, description="Session low.")
    close: float = Field(gt=0, description="Session close.")
    volume: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Contracts/shares traded. 0 is a genuine measurement (e.g. ^VIX, always 0); None "
            "means the vendor did not report volume at all. Never collapse the two."
        ),
    )
    source: str = Field(min_length=1, description="Provider name, e.g. 'yahoo-splitadj'.")
