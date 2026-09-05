"""Pydantic response models for the read API (T11).

These mirror the JSON shapes `app.gex.engine`'s dataclasses already produce via `.to_dict()`
(see that module's docstring: "Field names follow `frontend/src/api/types.ts` so that T11 is a
pass-through rather than a translation layer") plus the two merges T11 owns: `Snapshot.id` and
`Snapshot.is_eod`, which `app.gex.engine.SnapshotMeta` deliberately omits because it does no I/O.

Every field the engine can legitimately return as `None` (a wall, the flip point, an entire
`KeyLevels` row on a filter that admitted nothing) stays `X | None` here, never coerced to a
default -- see the engine and `GexLevel` docstrings for why turning a missing level into `0`
would misreport "no wall" as "wall at strike zero". Models are built with
`Model.model_validate(...)` against the engine's own `to_dict()` output (or a dict built from
it plus the snapshot-index merge), not by re-deriving fields by hand, so a change to the engine's
dict shape surfaces as a validation error here rather than silently drifting.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ChainResponse",
    "ContractOut",
    "ExpiryGexOut",
    "GexDiagnosticsOut",
    "GexResultOut",
    "KeyLevelsOut",
    "LevelHistoryRowOut",
    "ProfilePointOut",
    "SnapshotMetaOut",
    "StrikeGexOut",
]


class StrikeGexOut(BaseModel):
    strike: float
    call_gex: float
    put_gex: float
    net_gex: float
    abs_gex: float
    contracts: int
    open_interest: int


class ExpiryGexOut(BaseModel):
    expiry: dt.date
    dte: int
    call_gex: float
    put_gex: float
    net_gex: float
    abs_gex: float
    contracts: int
    open_interest: int


class ProfilePointOut(BaseModel):
    spot: float
    total_gex: float


class KeyLevelsOut(BaseModel):
    """Mirrors `app.gex.engine.KeyLevels.to_dict()` exactly.

    `call_wall` / `put_wall` / `max_abs_strike` / `flip_point` (and everything else here
    besides the four `*_gex` sums) are `None` whenever the filter admitted no contracts --
    the everyday `ZERO_DTE`-after-16:00-ET case -- or, for `flip_point` alone, whenever the
    ±10% gamma profile never changes sign. That is a real answer, not a missing one.
    """

    net_gex: float
    call_gex: float
    put_gex: float
    abs_gex: float
    call_wall: float | None
    call_wall_gex: float | None
    put_wall: float | None
    put_wall_gex: float | None
    max_abs_strike: float | None
    max_abs_gex: float | None
    max_net_strike: float | None
    min_net_strike: float | None
    max_call_gex_strike: float | None
    max_call_gex: float | None
    max_put_gex_strike: float | None
    max_put_gex: float | None
    flip_point: float | None
    spot: float | None
    computed_at: dt.datetime | None
    top_positive: tuple[StrikeGexOut, ...]
    top_negative: tuple[StrikeGexOut, ...]


class GexDiagnosticsOut(BaseModel):
    """Mirrors `app.gex.engine.GexDiagnostics.to_dict()` -- the audit trail for what a
    result's aggregates excluded and why. Not in `frontend/src/api/types.ts` at all (see the
    T11 divergence report); included because it is exactly what T10's validation reconciled
    against a vendor figure with, and the same reconciliation is a legitimate UI need.
    """

    contracts: int
    included: int
    expired: int
    missing_open_interest: int
    zero_open_interest: int
    missing_iv: int
    missing_iv_gex_vendor: float
    extreme_iv: int
    extreme_iv_open_interest: int
    extreme_iv_gex_excluded: float
    net_gex_iv_unfiltered: float
    iv_min_observed: float | None
    iv_max_observed: float | None
    iv_policy_mode: str
    iv_policy_min: float
    iv_policy_max: float
    use_vendor_gamma: bool
    extreme_iv_examples: tuple[str, ...]


class SnapshotMetaOut(BaseModel):
    """`app.gex.engine.SnapshotMeta.to_dict()` plus three fields that module's own docstring
    says are "storage concerns owned by T09/T11" and explicitly leaves out: `id`, `is_eod`,
    and (T34) `effective_at`. T11/T34 are the merge points named there.
    """

    id: int
    is_eod: bool
    underlying: str
    spot: float
    captured_at: dt.datetime
    source: str
    delayed_minutes: int
    contract_count: int
    effective_at: dt.datetime = Field(
        description=(
            "T34: the honest 'as of' instant for a staleness badge, derived from "
            "`captured_at` via `app.jobs.calendar.effective_data_time` -- equal to "
            "`captured_at` during a regular session, otherwise clamped to the most recent "
            "16:00 ET close plus `delayed_minutes`. `captured_at` itself is never mutated; "
            "it stays the vendor's raw payload timestamp (see `app.models.chain.ChainSnapshot`)."
        )
    )


class GexResultOut(BaseModel):
    """`GET /gex/{underlying}/latest` and `.../snapshots/{id}` response body.

    A near-verbatim `GexResult.to_dict()`, with `snapshot` upgraded to `SnapshotMetaOut`
    (the `id`/`is_eod` merge). `filter` is echoed back as the engine's own label -- one of
    the `ExpiryFilter` values, or `"EXPIRIES:<date>[,<date>...]"` for an explicit list -- so a
    client can round-trip it straight back into the `filter` query parameter.
    """

    model_config = ConfigDict()

    underlying: str
    filter: str
    spot: float
    snapshot: SnapshotMetaOut
    levels: KeyLevelsOut
    by_strike: tuple[StrikeGexOut, ...]
    by_expiry: tuple[ExpiryGexOut, ...]
    profile: tuple[ProfilePointOut, ...]
    diagnostics: GexDiagnosticsOut
    expiries: tuple[dt.date, ...]


class LevelHistoryRowOut(BaseModel):
    """`GET /gex/{underlying}/levels/history` row -- read straight from `gex_levels` joined
    to `snapshots`, never from Parquet (see that endpoint's own docstring for why).

    Superset of `frontend/src/api/types.ts`'s `LevelHistoryRow` GUESS: it additionally
    carries `call_wall_gex` / `put_wall_gex` / `max_call_gex_strike` / `max_put_gex_strike` /
    `computed_at`, all columns `GexLevel` already persists (see the T11 divergence report).
    """

    model_config = ConfigDict(from_attributes=True)

    snapshot_id: int
    captured_at: dt.datetime
    is_eod: bool
    filter: str
    net_gex: float | None
    call_wall: float | None
    call_wall_gex: float | None
    put_wall: float | None
    put_wall_gex: float | None
    max_abs_strike: float | None
    max_call_gex_strike: float | None
    max_put_gex_strike: float | None
    flip_point: float | None
    spot: float | None
    computed_at: dt.datetime


class ContractOut(BaseModel):
    """One contract, verbatim `OptionContract` fields (`backend/app/models/chain.py`).

    `open_interest: int | None` preserves the "0 (genuinely none) vs. None (unknown)"
    distinction all the way to JSON, per that module's own docstring. `iv` stays a decimal
    fraction. `expiry` and `last_trade_time` serialize as ISO 8601 (date / offset datetime).
    """

    model_config = ConfigDict(from_attributes=True)

    occ_symbol: str
    root: str
    underlying: str
    expiry: dt.date
    settlement: str
    strike: float
    right: str
    bid: float | None
    ask: float | None
    last: float | None
    volume: int | None
    open_interest: int | None
    iv: float | None
    delta: float | None
    gamma: float | None
    vega: float | None
    theta: float | None
    multiplier: int
    last_trade_time: dt.datetime | None


class ChainResponse(BaseModel):
    """`GET /chains/{underlying}/latest?expiry=` response: raw contracts for one expiry,
    wrapped with the same snapshot provenance every GEX response carries so a raw-chain view
    never needs a second fetch just to show "as of" / delay.
    """

    underlying: str
    expiry: dt.date
    snapshot: SnapshotMetaOut
    contracts: tuple[ContractOut, ...]
