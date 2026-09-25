"""Pydantic response models for the read API (T11).

These mirror the JSON shapes `app.modules.gex.gex.engine`'s dataclasses already produce via `.to_dict()`
(see that module's docstring: "Field names follow `frontend/src/api/types.ts` so that T11 is a
pass-through rather than a translation layer") plus the two merges T11 owns: `Snapshot.id` and
`Snapshot.is_eod`, which `app.modules.gex.gex.engine.SnapshotMeta` deliberately omits because it does no I/O.

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
    "CfdLevelOut",
    "CfdPlaybookEntryOut",
    "CfdPremiumCandidateOut",
    "CfdTranslationOut",
    "ChainResponse",
    "ContractOut",
    "DealerPositioningOut",
    "ExpiryGexOut",
    "ExpiryHistoryRowOut",
    "GexDiagnosticsOut",
    "GexResultOut",
    "IvRegimeOut",
    "KeyLevelsOut",
    "LevelHistoryRowOut",
    "LevelSetOut",
    "MaxPainOut",
    "PlaybookEntryOut",
    "PlaybookOut",
    "PremiumCandidateOut",
    "PremiumSellingOut",
    "ProfilePointOut",
    "PutCallRatiosOut",
    "ReportLevelOut",
    "ReportOut",
    "RiskAlertOut",
    "SnapshotMetaOut",
    "StrikeGexOut",
]


class StrikeGexOut(BaseModel):
    """Mirrors `engine.StrikeGex.to_dict()`.

    The four `net_gex_*` fields (T101) decompose `net_gex` by time to expiry and sum back to
    it exactly, so a client can answer "how much of this wall expires Friday" without a second
    request. Null on rows computed before T101, which is not zero.
    """

    strike: float
    call_gex: float
    put_gex: float
    net_gex: float
    abs_gex: float
    contracts: int
    open_interest: int
    net_gex_0dte: float | None = None
    net_gex_this_week: float | None = None
    net_gex_next_30d: float | None = None
    net_gex_beyond_30d: float | None = None


class ExpiryGexOut(BaseModel):
    expiry: dt.date
    dte: int
    call_gex: float
    put_gex: float
    net_gex: float
    abs_gex: float
    contracts: int
    open_interest: int


class ExpiryHistoryRowOut(BaseModel):
    """`GET /gex/{underlying}/expiry/history` row -- read straight from `gex_by_expiry` joined
    to `snapshots`, never from Parquet (T101).

    One row per expiry per snapshot: the term structure of dealer gamma as it stood at each
    capture. `dte` is calendar days from the snapshot's New York date.
    """

    model_config = ConfigDict(from_attributes=True)

    snapshot_id: int
    captured_at: dt.datetime
    is_eod: bool
    filter: str
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
    """Mirrors `app.modules.gex.gex.engine.KeyLevels.to_dict()` exactly.

    `call_wall` / `put_wall` / `max_abs_strike` / `flip_point` are `None` whenever the filter
    admitted no contracts -- the everyday `ZERO_DTE`-after-16:00-ET case -- or, for
    `flip_point` alone, whenever the ±10% gamma profile never changes sign. That is a real
    answer, not a missing one.

    **The four `*_gex` sums are nullable too, and used not to be** (T100). They previously
    arrived as `0.0` from an empty scope, which tells a client the book was measured and found
    flat rather than never measured at all -- two live snapshots shipped exactly that claim.
    """

    net_gex: float | None
    call_gex: float | None
    put_gex: float | None
    abs_gex: float | None
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
    """Mirrors `app.modules.gex.gex.engine.GexDiagnostics.to_dict()` -- the audit trail for what a
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
    net_gex_iv_unfiltered: float | None
    iv_min_observed: float | None
    iv_max_observed: float | None
    iv_policy_mode: str
    iv_policy_min: float
    iv_policy_max: float
    use_vendor_gamma: bool
    extreme_iv_examples: tuple[str, ...]


class SnapshotMetaOut(BaseModel):
    """`app.modules.gex.gex.engine.SnapshotMeta.to_dict()` plus three fields that module's own docstring
    says are "storage concerns owned by T09/T11" and explicitly leaves out: `id`, `is_eod`,
    and (T34) `effective_at`. T11/T34 are the merge points named there.
    """

    id: int | None = Field(
        description="The snapshot's index id; null for a live pull, which is never stored."
    )
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
            "`captured_at` via `app.modules.gex.jobs.calendar.effective_data_time` -- equal to "
            "`captured_at` during a regular session, otherwise clamped to the most recent "
            "16:00 ET close plus `delayed_minutes`. `captured_at` itself is never mutated; "
            "it stays the vendor's raw payload timestamp (see `app.modules.gex.models.chain.ChainSnapshot`)."
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


# --------------------------------------------------------------------------------------
# Report (T39) -- mirrors `app.modules.gex.gex.report`'s dataclasses via their own `to_dict()`
# --------------------------------------------------------------------------------------


class MaxPainOut(BaseModel):
    """`app.modules.gex.gex.report.MaxPain.to_dict()`.

    Every field is nullable together: a filter that admits no open contracts (the daily
    `ZERO_DTE`-after-the-close case) has no strike to minimise pain at, and `0` would read as
    "max pain is at strike zero" exactly the way a null wall would.
    """

    strike: float | None
    distance: float | None
    distance_pct: float | None
    total_pain: float | None
    strikes_evaluated: int
    contracts: int
    open_interest: int


class PutCallRatiosOut(BaseModel):
    """`app.modules.gex.gex.report.PutCallRatios.to_dict()`. Both ratios are puts / calls, and both are
    `None` rather than `inf` when the call side is zero."""

    call_open_interest: int
    put_open_interest: int
    total_open_interest: int
    open_interest_ratio: float | None
    call_volume: int
    put_volume: int
    total_volume: int
    volume_ratio: float | None
    call_contracts: int
    put_contracts: int
    missing_open_interest: int
    missing_volume: int


class IvRegimeOut(BaseModel):
    """`app.modules.gex.gex.report.IvRegime.to_dict()`.

    `label` is `None` whenever `history_observations < min_history_required`, which is the
    state of every deployment today -- nothing persists ATM IV history yet. The frontend must
    render "insufficient history" for that case, never substitute "NORMAL": inventing the band
    is the specific failure the example report made (T39).
    """

    atm_iv: float | None
    target_dte: int
    lower_dte: int | None
    upper_dte: int | None
    interpolated: bool
    contracts: int
    label: str | None
    history_observations: int
    min_history_required: int


class DealerPositioningOut(BaseModel):
    """`app.modules.gex.gex.report.DealerPositioning.to_dict()`.

    `direction` is `None` and `label` reads `NOISE-DOMINATED` whenever `ratio` is below
    `ratio_floor` -- DIA's everyday case (0.9 % against a 3 % floor). A UI that renders
    `direction` must handle the null as "no direction", not as a missing field to default.
    """

    net_gex: float | None
    abs_gex: float | None
    ratio: float | None
    ratio_floor: float | None
    noise_dominated: bool
    direction: str | None
    label: str
    description: str


class ReportLevelOut(BaseModel):
    """`app.modules.gex.gex.report.ReportLevel.to_dict()`. `side` is RESISTANCE, SUPPORT or STRADDLING."""

    strike: float | None
    net_gex: float | None
    abs_gex: float | None
    open_interest: int
    distance: float | None
    distance_pct: float | None
    side: str
    above_spot: bool


class LevelSetOut(BaseModel):
    """`app.modules.gex.gex.report.LevelSet.to_dict()`.

    `resistance` and `support` are guaranteed disjoint and correctly ordered -- every
    resistance strike is above every support strike. Strikes that would violate that live in
    `straddling` with `overlapping` set; see the dataclass docstring.
    """

    resistance: tuple[ReportLevelOut, ...]
    support: tuple[ReportLevelOut, ...]
    straddling: tuple[ReportLevelOut, ...]
    overlapping: bool
    overlap_note: str | None
    call_wall: float | None
    put_wall: float | None
    flip_point: float | None


class PremiumCandidateOut(BaseModel):
    """`app.modules.gex.gex.report.PremiumCandidate.to_dict()`. `mid` is null unless both sides quote."""

    occ_symbol: str
    strike: float | None
    right: str
    expiry: dt.date
    dte: int
    bid: float | None
    ask: float | None
    mid: float | None
    iv: float | None
    open_interest: int
    distance_pct: float | None


class PremiumSellingOut(BaseModel):
    """`app.modules.gex.gex.report.PremiumSelling.to_dict()` -- screening output, never a recommendation.

    Either side is legitimately empty when its wall sits far from spot; `note` says why. The
    renderer must show the note rather than an unexplained blank section.
    """

    calls: tuple[PremiumCandidateOut, ...]
    puts: tuple[PremiumCandidateOut, ...]
    dte_min: int
    dte_max: int
    call_boundary: float | None
    put_boundary: float | None
    note: str | None


class PlaybookEntryOut(BaseModel):
    """`app.modules.gex.gex.report.PlaybookEntry.to_dict()`.

    `trigger` / `target` / `invalidation` are computed levels or `None`. A null means no
    computed level sits there -- render a dash, never a derived number.
    """

    key: str
    name: str
    trigger: float | None
    trigger_label: str
    target: float | None
    target_label: str
    invalidation: float | None
    invalidation_label: str
    strategy: str


class PlaybookOut(BaseModel):
    """`app.modules.gex.gex.report.Playbook.to_dict()`. The range fields are populated only when spot
    actually sits between the two walls (`spot_in_range`)."""

    entries: tuple[PlaybookEntryOut, ...]
    range_low: float | None
    range_high: float | None
    range_magnet: float | None
    spot_in_range: bool


class RiskAlertOut(BaseModel):
    """`app.modules.gex.gex.report.RiskAlert.to_dict()`. `severity` is INFO or WARNING."""

    code: str
    severity: str
    message: str
    level: float | None


class CfdLevelOut(BaseModel):
    """`app.modules.gex.gex.report.CfdLevel.to_dict()` (T41). `strike` is the translated price;
    `native_strike` is the underlying's own, and `distance_pct` is passed through unchanged
    from the level it was translated from -- never recomputed, per the percentage-distance
    invariant."""

    side: str
    native_strike: float | None
    strike: float | None
    distance_pct: float | None


class CfdPlaybookEntryOut(BaseModel):
    """`app.modules.gex.gex.report.CfdPlaybookEntry.to_dict()` (T41). `key` matches the native
    `PlaybookEntryOut.key` it was translated from."""

    key: str
    trigger: float | None
    target: float | None
    invalidation: float | None


class CfdPremiumCandidateOut(BaseModel):
    """`app.modules.gex.gex.report.CfdPremiumCandidate.to_dict()` (T41). Only the strike translates --
    `occ_symbol` is the join key back to the native `PremiumCandidateOut`."""

    occ_symbol: str
    strike: float | None


class CfdTranslationOut(BaseModel):
    """`app.modules.gex.gex.report.CfdTranslation.to_dict()` (T41) -- the report re-expressed in the CFD
    instrument the user actually trades, anchored on `cfd_spot / underlying_spot`. `None` on
    `ReportOut.cfd` whenever the request omitted `cfd_spot`; this model never carries GEX
    magnitudes, premium prices or IV, because none of those convert (see the module docstring
    on `translate_to_cfd`)."""

    underlying: str
    instrument: str
    cfd_spot: float
    underlying_spot: float
    ratio: float
    call_wall: CfdLevelOut | None
    put_wall: CfdLevelOut | None
    flip_point: CfdLevelOut | None
    max_pain: CfdLevelOut | None
    resistance: tuple[CfdLevelOut, ...]
    support: tuple[CfdLevelOut, ...]
    straddling: tuple[CfdLevelOut, ...]
    playbook: tuple[CfdPlaybookEntryOut, ...]
    premium_calls: tuple[CfdPremiumCandidateOut, ...]
    premium_puts: tuple[CfdPremiumCandidateOut, ...]
    note: str


class ReportOut(BaseModel):
    """`GET /api/gex/report/{underlying}?filter=&cfd_spot=` response body.

    A verbatim `app.modules.gex.gex.report.ReportResult.to_dict()`, with `snapshot` upgraded to
    `SnapshotMetaOut` exactly as `GexResultOut` does -- the same `id` / `is_eod` /
    `effective_at` merge, so the report page can render T34's staleness badge from the
    identical fields the dashboard uses.

    `cfd` (T41) is `None` unless the request supplied `cfd_spot` -- the default, and every
    deployment's state today. It hangs off this model as one additional field rather than
    replacing anything above it, so a request with no `cfd_spot` gets a response identical to
    one from before T41 existed.
    """

    underlying: str
    filter: str
    spot: float
    generated_at: dt.datetime
    snapshot: SnapshotMetaOut
    max_pain: MaxPainOut
    ratios: PutCallRatiosOut
    iv_regime: IvRegimeOut
    positioning: DealerPositioningOut
    levels: LevelSetOut
    premium: PremiumSellingOut
    playbook: PlaybookOut
    alerts: tuple[RiskAlertOut, ...]
    summary: tuple[str, ...]
    cfd: CfdTranslationOut | None = None
