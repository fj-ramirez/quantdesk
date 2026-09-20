"""Normalized option-chain schema — the data contract every provider and consumer shares.

This module is the single source of truth for *what an option contract looks like* inside
this application. Providers (T02 Cboe, T03 MarketData.app, T22 Tradier) translate their
vendor payloads into these types; storage (T04 Parquet + Postgres index), the Greeks module
(T07) and the GEX engine (T08) consume them. Nothing here does I/O.

Design decisions, with reasoning
--------------------------------

**Pydantic models, frozen, over plain dataclasses.** This schema sits exactly at the trust
boundary where untrusted vendor JSON becomes application data, which is what pydantic is for:
the field constraints below (``iv > 0``, ``gamma >= 0``, tz-aware timestamps) turn a silent
provider mapping bug into an immediate, located exception. It also gives free
``model_dump()`` for the Parquet writer and free response models for the FastAPI layer.
Frozen because a snapshot is an immutable observation of the market at one instant; making
it mutable invites code that "fixes up" a chain in place and leaves two disagreeing copies.
``ChainSnapshot.contracts`` is a ``tuple`` for the same reason (any sequence is accepted at
construction; it is exposed as a tuple). Validation cost is roughly 0.3 s for a full 28.6k
contract SPX chain, which is acceptable against a 15-minute polling cadence.

**``strike`` is a ``float``, not a ``Decimal``.** The OCC symbol encodes the strike as an
integer number of thousandths of a dollar. Every such integer in the plausible range
(0 < strike <= 100_000, i.e. up to 100_000_000 thousandths) is far below 2**53, so
``int(digits) / 1000`` is a deterministic, injective map into ``float``: two different OCC
strikes can never collide on the same double, and ``round(strike * 1000)`` recovers the
original integer exactly. Grouping ``by_strike`` on floats is therefore safe. ``Decimal``
would be marginally more principled but would force object-dtype columns in pandas,
decimal128 columns in Parquet, and a conversion at the boundary of every NumPy call in
T07/T08 — cost with no accuracy gained. Use :func:`strike_to_occ_int` if you ever need the
exact integer back (e.g. for a database unique key).

**Implied volatility is a decimal fraction, never a percent.** ``iv=0.18`` means 18 %
annualized. Providers MUST normalize. Note for T02: contrary to the original task
description, the Cboe delayed-quotes endpoint already reports *per-contract* ``iv`` as a
decimal (verified 2026-09-04: the ATM SPX 2026-09-18 call quotes ``iv: 0.1064``). The
percent-like Cboe field is the index-level ``data.iv30`` (``11.276`` = 11.276 %), which this
schema does not carry. Do **not** divide the per-contract ``iv`` by 100.

**Greeks are stored exactly as a vendor reports them for a long holder of one contract,
per unit of underlying — no dealer sign applied.** ``gamma`` is the change in delta per
$1.00 (or 1.00 index point) move in the underlying, per share, and is therefore
non-negative for both calls and puts. ``delta`` is positive for calls and negative for puts.
The dealer-positioning sign convention (+1 for calls, −1 for puts) belongs to the GEX engine
(T08) and MUST NOT be baked in here, or it will be applied twice. Per PLAN.md §3:
``contract_gex = gamma * open_interest * multiplier * spot**2 * 0.01`` — dollars of dealer
gamma per 1 % move — with the ±1 factor applied by the engine.

**``multiplier`` is contracts → units of underlying (100 for all three symbols here).**
T08 must read ``contract.multiplier`` rather than hardcoding 100, so that adjusted contracts
(a corporate action can leave a non-100 multiplier) do not silently misprice.

**Missing vendor data is ``None``, never a fabricated zero.** Fields the schema cannot
function without — ``occ_symbol``, ``root``, ``underlying``, ``expiry``, ``settlement``,
``strike``, ``right``, ``multiplier`` — are required and are all derivable from the OCC
symbol alone, so no vendor can fail to supply them. Everything a vendor may legitimately not
have — quotes, volume, open interest, IV, Greeks, last trade time — is optional and defaults
to ``None``. The distinction matters most for ``open_interest``: ``0`` means "the vendor says
this contract has no open interest" (it contributes exactly zero GEX), while ``None`` means
"unknown" and the contract must be *excluded* from aggregates rather than treated as zero.
Constraints reject vendor sentinels that would otherwise masquerade as data: ``iv`` must be
strictly positive if present, so a provider seeing Cboe's ``iv: 0.0`` on an illiquid strike
(897 of 28,650 SPX contracts on 2026-09-04) must map it to ``None``, not pass it through.
``gamma: 0.0`` is *not* a sentinel — it is a genuine value rounded to four decimals — and is
accepted; this is one of the reasons PLAN.md §2 has the engine recompute Greeks locally.

**SPX and SPXW merge under a single ``SPX`` underlying** (PLAN.md §3 item 2). ``root`` keeps
the vendor's root verbatim; ``underlying`` is the canonical aggregation key that the engine
and API group by. The two are *not* redundant: SPX and SPXW contracts coexist on the same
expiry date with the same strike and right but different settlement (verified on the live
chain for 2026-09-18, 2026-10-16, 2026-11-20, 2026-12-18 and 2027-01-15). The natural key of
a contract is therefore ``(underlying, root, expiry, strike, right)`` — dropping ``root``
from a uniqueness constraint or a Parquet dedupe will destroy real contracts. Summing both
roots into one per-strike GEX bucket is correct and intended.

Settlement rule
---------------

``settlement`` is derived from the **root**, never from the expiry date:

* ``SPX`` → ``AM``. The A.M.-settled index series, settled against the SET opening print at
  09:30 NY on the expiry date.
* ``SPXW`` → ``PM``. The weekly/end-of-month series, settled against the 16:00 NY close.
* ``SPY``, ``QQQ`` and any other equity/ETF root → ``PM``, settled at the 16:00 NY close.

Two facts from the live chain rule out the tempting "third Friday ⇒ AM" shortcut: third
Fridays carry *both* an AM-settled ``SPX`` series and a PM-settled ``SPXW`` series at the
same strikes, and at least one listed ``SPX`` expiry (2027-06-17) is not a Friday at all.
T07 turns this into a time to expiry: AM expires 09:30 America/New_York, PM expires 16:00
America/New_York, on the ``expiry`` date.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Annotated, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "AM_SETTLED_ROOTS",
    "DEFAULT_MULTIPLIER",
    "OCC_TAIL_LEN",
    "STRIKE_SCALE",
    "ChainSnapshot",
    "OccSymbol",
    "OptionContract",
    "Right",
    "Settlement",
    "Underlying",
    "parse_occ_symbol",
    "settlement_for_root",
    "strike_to_occ_int",
    "underlying_for_root",
]

#: Contracts per unit of underlying for every symbol in scope (SPX, SPY, QQQ, GLD, DIA).
DEFAULT_MULTIPLIER = 100

#: Fixed width of the ``YYMMDD`` + ``C|P`` + 8-digit-strike tail of an OCC symbol.
OCC_TAIL_LEN = 15

#: The OCC strike field is an integer number of thousandths of a dollar.
STRIKE_SCALE = 1000


class Right(StrEnum):
    """Option right. Values are the single letters used by the OCC symbol itself."""

    CALL = "C"
    PUT = "P"


class Settlement(StrEnum):
    """When the contract settles, which fixes its expiry *time* for T07's Greeks.

    ``AM`` settles against the opening print at 09:30 America/New_York on the expiry date;
    ``PM`` settles against the 16:00 America/New_York close.
    """

    AM = "AM"
    PM = "PM"


class Underlying(StrEnum):
    """Canonical aggregation key. SPX and SPXW roots both map to :attr:`SPX`.

    Deliberately closed rather than a free string so that a provider typo (``"SPXW"`` passed
    as an underlying, say) fails loudly. Adding an instrument is a one-line change here plus
    an entry in :data:`_ROOT_TO_UNDERLYING`.
    """

    SPX = "SPX"
    SPY = "SPY"
    QQQ = "QQQ"
    #: Added T38. Commodity ETF (gold bullion trust), single vendor root == ticker, P.M.
    #: settled. Verified live 2026-09-05: 7,546 contracts, 29 expiries, no engine change.
    GLD = "GLD"
    #: Added T38. Dow Jones Industrial Average ETF, single vendor root == ticker, P.M.
    #: settled. Verified live 2026-09-05: 5,028 contracts, 21 expiries. See docs/validation.md
    #: for the carry-parameter caveat on this symbol's net GEX and flip point.
    DIA = "DIA"

    # --- T47: sector and industry ETFs, captured by the separate 16:45 ET extended job ------
    # (plans/continuation/03-regime-board.md). Every symbol below was verified live against
    # `https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json` on 2026-09-09:
    # single vendor root == ticker (no adjusted-option variant, unlike SPX/SPXW), P.M. settled
    # like every other equity/ETF root, decimal-fraction per-contract IV (median under 1.0 on
    # every symbol, confirming the same units as SPX/SPY/GLD/DIA), and zero missing-OI
    # contracts on all 23 -- cleaner data than SPX's own chain. None failed verification, so
    # `Settings.EXTENDED_SYMBOLS`'s default carries all 23. See docs/validation.md for the
    # dividend-yield sensitivity measurement this task's plan calls for.
    #
    #: The 11 Select Sector SPDRs. Contract / expiry counts as observed 2026-09-09.
    XLK = "XLK"  #: Technology. 2,336 contracts, 17 expiries.
    XLF = "XLF"  #: Financials. 2,028 contracts, 28 expiries.
    XLE = "XLE"  #: Energy. 2,070 contracts, 25 expiries.
    XLV = "XLV"  #: Health Care. 1,464 contracts, 13 expiries.
    XLI = "XLI"  #: Industrials. 1,934 contracts, 13 expiries.
    XLY = "XLY"  #: Consumer Discretionary. 1,318 contracts, 12 expiries.
    XLP = "XLP"  #: Consumer Staples. 1,078 contracts, 13 expiries.
    XLU = "XLU"  #: Utilities. 1,004 contracts, 15 expiries.
    XLB = "XLB"  #: Materials. 922 contracts, 12 expiries.
    #: Real Estate -- the thinnest of the eleven, as the plan predicted. 222 contracts, 5
    #: expiries. Still a real, tradeable chain; left in rather than dropped.
    XLRE = "XLRE"
    XLC = "XLC"  #: Communication Services. 964 contracts, 11 expiries.
    #: Broad and single-industry ETFs beyond the sector SPDRs.
    IWM = "IWM"  #: Russell 2000 (small caps). 4,840 contracts, 32 expiries.
    SMH = "SMH"  #: Semiconductors. 6,386 contracts, 27 expiries.
    XBI = "XBI"  #: Biotech. 2,088 contracts, 14 expiries.
    KRE = "KRE"  #: Regional banks. 1,552 contracts, 20 expiries.
    XOP = "XOP"  #: Oil & gas exploration. 2,020 contracts, 15 expiries.
    TLT = "TLT"  #: 20+ year Treasury bond. 2,472 contracts, 30 expiries.
    HYG = "HYG"  #: High-yield corporate bond. 1,294 contracts, 19 expiries.
    EEM = "EEM"  #: Emerging markets. 2,024 contracts, 24 expiries.
    FXI = "FXI"  #: China large-cap. 1,384 contracts, 22 expiries.
    SLV = "SLV"  #: Silver bullion trust. 4,854 contracts, 27 expiries.
    USO = "USO"  #: Crude oil. 4,628 contracts, 21 expiries.
    GDX = "GDX"  #: Gold miners. 3,042 contracts, 17 expiries.


#: Roots whose contracts are A.M.-settled. Everything else is P.M.-settled. See the module
#: docstring: this is a root-based rule, not a date-based one.
AM_SETTLED_ROOTS = frozenset({"SPX"})

#: Vendor root → canonical underlying. Only roots actually observed on the live feeds are
#: listed; an unknown root raises rather than being guessed at, so that a new listing (an
#: SPXQ quarterly, say) surfaces as a provider error instead of silently polluting SPX.
_ROOT_TO_UNDERLYING: dict[str, Underlying] = {
    "SPX": Underlying.SPX,
    "SPXW": Underlying.SPX,
    "SPY": Underlying.SPY,
    "QQQ": Underlying.QQQ,
    "GLD": Underlying.GLD,
    "DIA": Underlying.DIA,
    # T47: every sector/industry ETF root equals its own Underlying value, verified live
    # 2026-09-09 (see the Underlying enum above) -- none of the third-Friday AM/PM-dual-series
    # complexity SPX/SPXW has, so this is a plain one-to-one map.
    "XLK": Underlying.XLK,
    "XLF": Underlying.XLF,
    "XLE": Underlying.XLE,
    "XLV": Underlying.XLV,
    "XLI": Underlying.XLI,
    "XLY": Underlying.XLY,
    "XLP": Underlying.XLP,
    "XLU": Underlying.XLU,
    "XLB": Underlying.XLB,
    "XLRE": Underlying.XLRE,
    "XLC": Underlying.XLC,
    "IWM": Underlying.IWM,
    "SMH": Underlying.SMH,
    "XBI": Underlying.XBI,
    "KRE": Underlying.KRE,
    "XOP": Underlying.XOP,
    "TLT": Underlying.TLT,
    "HYG": Underlying.HYG,
    "EEM": Underlying.EEM,
    "FXI": Underlying.FXI,
    "SLV": Underlying.SLV,
    "USO": Underlying.USO,
    "GDX": Underlying.GDX,
}


def underlying_for_root(root: str) -> Underlying:
    """Map a vendor root to the canonical underlying used for aggregation.

    ``SPX`` and ``SPXW`` both map to :attr:`Underlying.SPX`, which is how PLAN.md §3 item 2's
    "SPX and SPXW roots merged" requirement is satisfied.

    Raises:
        ValueError: if the root is not one this application knows how to classify. Providers
            should let this propagate (or skip the contract and log it) rather than guess.
    """
    try:
        return _ROOT_TO_UNDERLYING[root.upper()]
    except KeyError:
        raise ValueError(
            f"unknown option root {root!r}; add it to _ROOT_TO_UNDERLYING and, if it is "
            f"A.M.-settled, to AM_SETTLED_ROOTS"
        ) from None


def settlement_for_root(root: str) -> Settlement:
    """Return the settlement style implied by a root. See the module docstring for the rule."""
    return Settlement.AM if root.upper() in AM_SETTLED_ROOTS else Settlement.PM


def strike_to_occ_int(strike: float) -> int:
    """Recover the exact OCC integer strike (thousandths of a dollar) from a float strike.

    Exact for every strike this application sees, because ``int -> int/1000 -> round(x*1000)``
    round-trips for integers well below 2**53. Useful for database keys and for comparing
    strikes without relying on float equality.
    """
    return round(strike * STRIKE_SCALE)


class OccSymbol(NamedTuple):
    """Parsed OCC symbol. A tuple ``(root, expiry, right, strike)`` with named access."""

    root: str
    expiry: dt.date
    right: Right
    strike: float


def parse_occ_symbol(s: str) -> OccSymbol:
    """Parse an OCC option symbol into ``(root, expiry, right, strike)``.

    The OCC standard is a 21-character string: a root left-justified and **space-padded to
    six characters**, then ``YYMMDD``, then ``C`` or ``P``, then an 8-digit strike in
    thousandths of a dollar. Vendor JSON generally omits the padding and emits a variable
    length string instead (Cboe sends ``SPXW260904P07700000``, 19 characters). Both forms are
    accepted, as are lowercase symbols and surrounding whitespace.

    The tail is parsed by fixed width **from the right** rather than by a greedy match from
    the left. That is not a stylistic choice: adjusted-option roots may end in a digit
    (``SPY1``), so a left-anchored ``[A-Z]+`` match against a digit-bearing root is ambiguous,
    while the 15-character tail never is.

    Two-digit years map to 2000–2099, which is the OCC symbology's own assumption.

    Args:
        s: An OCC symbol, padded or unpadded, in any case.

    Returns:
        :class:`OccSymbol` — ``(root, expiry, right, strike)``. ``strike`` is a float in
        dollars/index points; ``right`` is a :class:`Right`.

    Raises:
        ValueError: on anything that is not a well-formed OCC symbol — wrong length, a root
            that is empty, over six characters or not alphanumeric, a non-date ``YYMMDD``, a
            right other than C/P, a non-numeric or zero strike. Providers feed this whatever
            the vendor sends, so it is strict on purpose: a symbol that does not parse is a
            contract this application cannot price, not something to guess at.

    Examples:
        >>> parse_occ_symbol("SPX260918C00200000")
        OccSymbol(root='SPX', expiry=datetime.date(2026, 9, 18), right=<Right.CALL: 'C'>, strike=200.0)
        >>> parse_occ_symbol("SPXW  260904P07700000").strike
        7700.0
    """
    if not isinstance(s, str):
        # ValueError rather than TypeError on purpose. A non-string here is vendor data (a
        # JSON null or number in the symbol field), not a programming error, so it belongs to
        # the single documented failure mode a provider wraps in one `except ValueError`.
        raise ValueError(f"OCC symbol must be a string, got {type(s).__name__}")  # noqa: TRY004

    symbol = s.strip().upper()
    if len(symbol) < OCC_TAIL_LEN + 1:
        raise ValueError(f"OCC symbol too short: {s!r}")

    root = symbol[:-OCC_TAIL_LEN].rstrip()
    tail = symbol[-OCC_TAIL_LEN:]

    if not root:
        raise ValueError(f"OCC symbol has an empty root: {s!r}")
    if len(root) > 6:
        raise ValueError(f"OCC root {root!r} exceeds six characters: {s!r}")
    if not root.isalnum() or not root[0].isalpha():
        raise ValueError(f"OCC root {root!r} is not alphanumeric starting with a letter: {s!r}")

    date_part, right_part, strike_part = tail[:6], tail[6], tail[7:]

    if not date_part.isdigit():
        raise ValueError(f"OCC expiry {date_part!r} is not six digits: {s!r}")
    try:
        expiry = dt.date(2000 + int(date_part[:2]), int(date_part[2:4]), int(date_part[4:6]))
    except ValueError as exc:
        raise ValueError(f"OCC expiry {date_part!r} is not a valid date: {s!r} ({exc})") from None

    try:
        right = Right(right_part)
    except ValueError:
        raise ValueError(f"OCC right {right_part!r} is not 'C' or 'P': {s!r}") from None

    if not strike_part.isdigit():
        raise ValueError(f"OCC strike {strike_part!r} is not eight digits: {s!r}")
    strike_int = int(strike_part)
    if strike_int <= 0:
        raise ValueError(f"OCC strike must be positive: {s!r}")

    return OccSymbol(root=root, expiry=expiry, right=right, strike=strike_int / STRIKE_SCALE)


def _as_utc(value: dt.datetime) -> dt.datetime:
    """Require an aware datetime and normalize it to UTC.

    Naive datetimes are rejected rather than assumed to be UTC. The Cboe payload is the
    cautionary tale: its top-level ``timestamp`` is naive **UTC** while every
    ``last_trade_time`` is naive **America/New_York**, so any blanket assumption is wrong for
    half the fields. Providers must attach the correct tzinfo themselves.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "datetime must be timezone-aware; attach the vendor's actual timezone before "
            "constructing the model (Cboe: 'timestamp' is UTC, 'last_trade_time' is "
            "America/New_York)"
        )
    return value.astimezone(dt.UTC)


NonNegFloat = Annotated[float, Field(ge=0)]


class OptionContract(BaseModel):
    """One option contract as observed in a single chain snapshot.

    Immutable. Identity fields (everything down to :attr:`right`) are always present and are
    all derivable from :attr:`occ_symbol`; market-data fields below them are ``None`` when the
    vendor did not report them. See the module docstring for units, sign conventions and the
    ``None``-versus-zero rule.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    occ_symbol: str = Field(
        min_length=OCC_TAIL_LEN + 1,
        description="OCC symbol as normalized by parse_occ_symbol (uppercase, unpadded root).",
    )
    root: str = Field(
        min_length=1, max_length=6, description="Vendor root, e.g. 'SPX', 'SPXW', 'SPY'."
    )
    underlying: Underlying = Field(
        description="Canonical aggregation key; SPX and SPXW both merge to SPX."
    )
    expiry: dt.date = Field(description="Expiry date. The expiry *time* follows `settlement`.")
    settlement: Settlement = Field(description="AM (09:30 NY) or PM (16:00 NY); root-derived.")
    strike: float = Field(gt=0, description="Strike in dollars / index points.")
    right: Right

    bid: NonNegFloat | None = Field(default=None, description="Best bid, dollars per unit.")
    ask: NonNegFloat | None = Field(default=None, description="Best ask, dollars per unit.")
    last: NonNegFloat | None = Field(default=None, description="Last trade price per unit.")
    volume: int | None = Field(default=None, ge=0, description="Contracts traded this session.")
    open_interest: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Open contracts, as published by OCC overnight. 0 means genuinely zero; None "
            "means unknown and the contract must be excluded from GEX aggregates."
        ),
    )
    iv: float | None = Field(
        default=None,
        gt=0,
        description="Implied volatility as a DECIMAL fraction (0.18 == 18%), never a percent.",
    )
    delta: float | None = Field(
        default=None, description="Long-holder delta per unit: positive calls, negative puts."
    )
    gamma: NonNegFloat | None = Field(
        default=None,
        description="Delta change per 1.00 move in the underlying, per unit. Unsigned.",
    )
    vega: NonNegFloat | None = Field(
        default=None, description="Vendor-defined vega (typically per 1 volatility point)."
    )
    theta: float | None = Field(
        default=None, description="Vendor-defined theta, typically per calendar day. Usually < 0."
    )
    multiplier: int = Field(
        default=DEFAULT_MULTIPLIER, gt=0, description="Units of underlying per contract."
    )
    last_trade_time: dt.datetime | None = Field(
        default=None, description="Time of the last trade, tz-aware, normalized to UTC."
    )

    @field_validator("occ_symbol", "root", mode="before")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("last_trade_time")
    @classmethod
    def _utc(cls, v: dt.datetime | None) -> dt.datetime | None:
        return None if v is None else _as_utc(v)

    @classmethod
    def from_occ(
        cls,
        occ_symbol: str,
        *,
        underlying: Underlying | str | None = None,
        bid: float | None = None,
        ask: float | None = None,
        last: float | None = None,
        volume: int | None = None,
        open_interest: int | None = None,
        iv: float | None = None,
        delta: float | None = None,
        gamma: float | None = None,
        vega: float | None = None,
        theta: float | None = None,
        multiplier: int = DEFAULT_MULTIPLIER,
        last_trade_time: dt.datetime | None = None,
    ) -> Self:
        """Build a contract from its OCC symbol plus whatever market data the vendor gave.

        Providers should use this rather than assembling :class:`OptionContract` by hand, so
        that root, underlying, expiry, settlement, strike and right are derived identically
        everywhere. Pass ``None`` — not ``0`` — for anything the vendor omitted or reported as
        a sentinel; in particular map a vendor ``iv`` of ``0`` to ``None``.

        Args:
            occ_symbol: The vendor's OCC symbol, padded or unpadded.
            underlying: Override the root-derived underlying. Only needed if a vendor lists a
                contract under a root this module does not map.

        Raises:
            ValueError: if the symbol does not parse or the root is unknown.
        """
        parsed = parse_occ_symbol(occ_symbol)
        return cls(
            occ_symbol=f"{parsed.root}{occ_symbol.strip().upper()[-OCC_TAIL_LEN:]}",
            root=parsed.root,
            underlying=Underlying(underlying) if underlying else underlying_for_root(parsed.root),
            expiry=parsed.expiry,
            settlement=settlement_for_root(parsed.root),
            strike=parsed.strike,
            right=parsed.right,
            bid=bid,
            ask=ask,
            last=last,
            volume=volume,
            open_interest=open_interest,
            iv=iv,
            delta=delta,
            gamma=gamma,
            vega=vega,
            theta=theta,
            multiplier=multiplier,
            last_trade_time=last_trade_time,
        )


class ChainSnapshot(BaseModel):
    """A full option chain for one underlying, observed at one instant.

    This is the unit of storage (one Parquet file per snapshot, T04) and the input to the GEX
    engine (T08). ``captured_at`` is the vendor's own timestamp for the payload -- **not**
    reliably the time the data itself became effective (T34 correction: an earlier version of
    this docstring claimed it was). Cboe's ``timestamp`` field is payload-*generation* time: it
    keeps advancing on every request, including hours after the 16:00 ET close, while the
    quotes it describes are frozen at that close. This module still stores it verbatim, on
    purpose -- providers should use the vendor's own clock rather than the HTTP response time
    either way, and every existing consumer that keys off ``captured_at`` (the duplicate-
    capture check in `app.modules.gex.jobs.capture`, T29's per-day `is_eod` guard, `levels/history`'s
    date-range query) only needs it to be NY-date-correct and roughly monotonic, which it is.
    A consumer that needs an honest "as of" instant for a staleness badge -- i.e. one that
    accounts for the market having been closed -- should derive it via
    :func:`app.modules.gex.jobs.calendar.effective_data_time` rather than trust this field directly once
    the market may have closed; see :class:`app.modules.gex.api.schemas.SnapshotMetaOut` for where that
    derived value is exposed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    underlying: Underlying
    spot: float = Field(gt=0, description="Underlying price at capture time.")
    captured_at: dt.datetime = Field(
        description=(
            "Vendor's own payload timestamp, tz-aware and normalized to UTC. NOT reliably "
            "the data's effective time once the market may be closed (T34) -- see the class "
            "docstring and app.modules.gex.jobs.calendar.effective_data_time."
        )
    )
    source: str = Field(min_length=1, description="Provider name, e.g. 'cboe'.")
    delayed_minutes: int = Field(
        ge=0, description="Vendor delay in minutes; 0 means real-time. Cboe delayed JSON is 15."
    )
    contracts: tuple[OptionContract, ...] = Field(
        default=(), description="Every contract in the chain. Accepts any sequence."
    )

    @field_validator("captured_at")
    @classmethod
    def _utc(cls, v: dt.datetime) -> dt.datetime:
        return _as_utc(v)

    @model_validator(mode="after")
    def _contracts_match_underlying(self) -> Self:
        """Catch a provider mixing symbols into one snapshot before it reaches storage."""
        mismatched = {c.underlying for c in self.contracts} - {self.underlying}
        if mismatched:
            raise ValueError(
                f"snapshot for {self.underlying} contains contracts for "
                f"{sorted(m.value for m in mismatched)}"
            )
        return self

    def __len__(self) -> int:
        return len(self.contracts)

    @property
    def expiries(self) -> tuple[dt.date, ...]:
        """Distinct expiry dates present, ascending."""
        return tuple(sorted({c.expiry for c in self.contracts}))

    @property
    def roots(self) -> tuple[str, ...]:
        """Distinct vendor roots present, e.g. ``('SPX', 'SPXW')``."""
        return tuple(sorted({c.root for c in self.contracts}))
