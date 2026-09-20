"""Normalized daily-bar schema (T42, plans/continuation/00-foundation-daily-bars.md).

Mirrors the design of `app.modules.gex.models.chain`: a small, frozen Pydantic model sits at the trust
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

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["DailyBar", "IntradayBar", "LiveQuote"]


class IntradayBar(BaseModel):
    """One interval-aligned intraday OHLCV bucket (T74).

    Distinct from `DailyBar` in exactly one way that matters: it is keyed by an **instant**
    (`ts`, the bucket's opening moment, tz-aware UTC) rather than a trading date, so an
    interval has to travel with it. Everything else -- the plain-ticker `symbol` contract, the
    nullable `volume` meaning "unknown, not zero", the `source` tag -- follows `DailyBar`
    exactly, on purpose: the two are read side by side and a reader should not have to hold two
    conventions in mind.

    A bucket in here is always interval-aligned. Yahoo's synthetic trailing live-quote row
    (verified 2026-09-11: stamped 15:14:17 with `volume = 0`) is not a bucket and is never
    modelled as one -- the provider returns it separately. See
    `plans/continuous-feed/05-intraday-bars.md`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, description="Plain ticker as this app knows it (SPY, ^VIX, SPX).")
    interval: str = Field(min_length=1, max_length=8, description="Vendor interval, e.g. '5m'.")
    ts: dt.datetime = Field(description="Bucket opening instant, tz-aware, normalized to UTC.")
    open: float = Field(gt=0, description="Bucket open.")
    high: float = Field(gt=0, description="Bucket high.")
    low: float = Field(gt=0, description="Bucket low.")
    close: float = Field(gt=0, description="Bucket close; still moving for the newest bucket.")
    volume: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Contracts/shares traded in the bucket. `None` means the vendor published no "
            "volume for it (^VIX never does); `0` means genuinely none traded."
        ),
    )
    source: str = Field(min_length=1, description="Provider name, e.g. 'yahoo-splitadj'.")

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("IntradayBar.ts must be timezone-aware")
        return v.astimezone(dt.UTC)


class LiveQuote(BaseModel):
    """The vendor's trailing live-quote row -- a price at an instant, explicitly **not** a bar.

    Yahoo appends this to every intraday payload during a session: an unaligned timestamp and
    `volume = 0`, carrying the current quote. Persisting it as a bucket would put a
    zero-volume bar at a ragged timestamp in the middle of the series, so it is given its own
    type rather than being squeezed into `IntradayBar` with a flag -- a shape that cannot be
    mistaken for a bar is better than one that must be remembered not to be.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    ts: dt.datetime = Field(description="Quote instant, tz-aware, normalized to UTC.")
    price: float = Field(gt=0)
    source: str = Field(min_length=1)

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("LiveQuote.ts must be timezone-aware")
        return v.astimezone(dt.UTC)


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
