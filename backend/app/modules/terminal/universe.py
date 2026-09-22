"""The series universe (spec 1.3), as data.

Every series the tool intends to carry is registered here, including the ones no
Phase 1 adapter can fill. That is deliberate: a universe that silently contains
only what happened to be fetchable hides its own gaps. Series with no adapter
yet are registered with source set to their eventual source and a note saying
which phase supplies them; the backfill reports them as no_adapter rather than
omitting them.

Every FRED code below was verified live against the API on 2026-09-12. Codes
that did NOT survive that check are recorded in RETIRED_CODES with what replaced
them, so the next person to reach for them finds the answer instead of a 400.
"""

from __future__ import annotations

from .models import SeriesMeta

# Codes checked and found dead. Kept as documentation, never fetched.
RETIRED_CODES = {
    "GOLDPMGBD228NLBM": "LBMA gold PM fix; retired from FRED. No free daily "
                        "gold on FRED; needs the prices adapter (phase 4).",
    "GOLDAMGBD228NLBM": "LBMA gold AM fix; retired from FRED. Same as above.",
    "RU2000PR": "Russell 2000 price index; not available on FRED. Needs the "
                "prices adapter (phase 4).",
}

# Sources with a working adapter. Anything else is registered but not fetched.
#
# T91 added "prices", which is unlike the other four: it reads `gex.daily_bars` in the sibling
# schema rather than a vendor over HTTP. It is a source in every sense that matters here --
# codes resolve, batches are recorded, failures are reported -- and making it one is what let
# three series that had been pending since phase 1 fill without a new vendor.
FETCHABLE_SOURCES = frozenset({"fred", "treasury", "cboe", "cftc", "prices"})

# Series computed from other stored series rather than fetched. Filled by
# `xactx derive`; see derive.DERIVATIONS for the recipes.
DERIVED_SOURCE = "derived"

# Series solved from a user-supplied settlement strip by `xactx policy`, not by
# a derive recipe. Distinct from DERIVED_SOURCE because the producer and the
# input are different: derive reads the store, policy reads a file the user
# supplies (see adapters/cme.py for why it cannot fetch).
POLICY_SOURCE = "policy"


def _fred(
    series_id: str,
    code: str,
    name: str,
    asset_class: str,
    category: str,
    unit: str,
    transform: str,
    *,
    frequency: str = "d",
    revisable: bool = False,
    vintage_source: str = "source_vintage",
    notes: str | None = None,
) -> SeriesMeta:
    return SeriesMeta(
        series_id=series_id,
        display_name=name,
        source="fred",
        source_code=code,
        asset_class=asset_class,
        category=category,
        unit=unit,
        frequency=frequency,
        default_transform=transform,
        revisable=revisable,
        # ALFRED gives a real publication vintage for most series; the
        # exceptions are listed in adapters.fred.NO_ALFRED_CODES.
        vintage_source=vintage_source,
        snapshot_tz="America/New_York",
        snapshot_local_time="16:00",
        notes=notes,
    )


def _treasury(series_id: str, code: str, name: str) -> SeriesMeta:
    return SeriesMeta(
        series_id=series_id,
        display_name=name,
        source="treasury",
        source_code=code,
        asset_class="rates",
        category="level",
        unit="pct",
        frequency="d",
        default_transform="diff",
        revisable=False,
        # No revision history published; as_of is value_date + publication lag.
        vintage_source="derived_lag",
        snapshot_tz="America/New_York",
        snapshot_local_time="18:00",
        notes="Cross-check against the FRED H.15 equivalent (spec 1.2). "
              "Compared, never merged.",
    )


def _cboe(series_id: str, code: str, name: str, notes: str | None = None) -> SeriesMeta:
    return SeriesMeta(
        series_id=series_id,
        display_name=name,
        source="cboe",
        source_code=code,
        asset_class="vol",
        category="implied",
        unit="index",
        frequency="d",
        default_transform="diff",
        revisable=False,
        vintage_source="derived_lag",
        snapshot_tz="America/New_York",
        snapshot_local_time="17:00",
        notes=notes,
    )


def _derived(
    series_id: str,
    name: str,
    asset_class: str,
    category: str,
    unit: str,
    transform: str,
    vintage_source: str,
    note: str,
    source: str = DERIVED_SOURCE,
    frequency: str = "d",
) -> SeriesMeta:
    """A series computed from other stored series (see derive.py).

    vintage_source records the dominant basis of its INPUTS. The authoritative
    answer is per-row: a derived observation inherits the weakest as_of_basis of
    the inputs that produced it.
    """
    return SeriesMeta(
        series_id=series_id,
        display_name=name,
        source=source,
        source_code="",
        asset_class=asset_class,
        category=category,
        unit=unit,
        frequency=frequency,
        default_transform=transform,
        revisable=False,
        vintage_source=vintage_source,
        snapshot_tz="America/New_York",
        snapshot_local_time="16:00",
        notes=note,
    )


def _cftc(series_id: str, code: str, name: str, note: str) -> SeriesMeta:
    """A COT net large-speculator position, in raw contracts.

    Weekly, Tuesday-dated, published the following Friday. The three-day
    information lag lives in the adapter's as_of, not here.
    """
    return SeriesMeta(
        series_id=series_id,
        display_name=name,
        source="cftc",
        source_code=code,
        asset_class="macro",
        category="positioning",
        unit="contracts",
        frequency="w",
        default_transform="level",
        revisable=False,
        vintage_source="derived_lag",
        snapshot_tz="America/New_York",
        snapshot_local_time="15:30",
        notes=note,
    )


def _pending(
    series_id: str,
    name: str,
    source: str,
    asset_class: str,
    category: str,
    unit: str,
    transform: str,
    note: str,
    *,
    frequency: str = "d",
) -> SeriesMeta:
    """A series the universe carries but no Phase 1 adapter can fill."""
    return SeriesMeta(
        series_id=series_id,
        display_name=name,
        source=source,
        source_code="",
        asset_class=asset_class,
        category=category,
        unit=unit,
        frequency=frequency,
        default_transform=transform,
        revisable=False,
        vintage_source="ingest_time",
        snapshot_tz=None,
        snapshot_local_time=None,
        notes=note,
    )


def _prices(
    series_id: str,
    symbol: str,
    name: str,
    tracks: str,
    asset_class: str,
    category: str,
    unit: str,
    transform: str,
) -> SeriesMeta:
    """A series filled from an ETF proxy in `gex.daily_bars` (T91).

    `display_name` and `notes` both name the proxy, and neither is decoration: the value
    stored is the ETF's close, not the index level, and a reader who takes it for the index
    will be wrong by the tracking error, the expense ratio and every distribution ever paid.
    Correlations and betas do not care; a level does.

    `vintage_source="derived_lag"` because the bars table carries no revision history -- the
    `as_of` is the value date at the bars job's 17:30 ET run, per `adapters/prices.py`.
    """
    return SeriesMeta(
        series_id=series_id,
        display_name=name,
        source="prices",
        source_code=symbol,
        asset_class=asset_class,
        category=category,
        unit=unit,
        frequency="d",
        default_transform=transform,
        revisable=False,
        vintage_source="derived_lag",
        snapshot_tz="America/New_York",
        snapshot_local_time="17:30",
        notes=(
            f"ETF proxy: {symbol} close from gex.daily_bars, not {tracks} itself. "
            f"Carries {symbol}'s tracking error, expense ratio and distributions; use for "
            f"returns and correlation, not as a level of {tracks}."
        ),
    )


# --- Rates (spec 1.3) --------------------------------------------------------
RATES = [
    _fred("ust.3m.nominal", "DGS3MO", "UST 3m nominal yield", "rates", "level", "pct", "diff"),
    _fred("ust.2y.nominal", "DGS2", "UST 2y nominal yield", "rates", "level", "pct", "diff"),
    _fred("ust.5y.nominal", "DGS5", "UST 5y nominal yield", "rates", "level", "pct", "diff"),
    _fred("ust.10y.nominal", "DGS10", "UST 10y nominal yield", "rates", "level", "pct", "diff"),
    _fred("ust.30y.nominal", "DGS30", "UST 30y nominal yield", "rates", "level", "pct", "diff"),
    _fred("ust.5y.real", "DFII5", "UST 5y TIPS real yield", "rates", "level", "pct", "diff",
          notes="Starts 2003-01-02."),
    _fred("ust.10y.real", "DFII10", "UST 10y TIPS real yield", "rates", "level", "pct", "diff",
          notes="Starts 2003-01-02."),
    _fred("spread.2s10s", "T10Y2Y", "2s10s spread", "rates", "spread", "pct", "diff"),
    _fred("rates.effr", "EFFR", "Effective federal funds rate", "rates", "level", "pct",
          "diff",
          notes="The rate ZQ futures settle on, and therefore the spot anchor "
                "for the implied policy path (spec 2.1). Starts 2000-07-03."),
]

# --- Inflation expectations --------------------------------------------------
INFLATION = [
    _fred("be.5y", "T5YIE", "5y breakeven inflation", "rates", "implied", "pct", "diff",
          notes="Contains a liquidity premium; not a clean expectation (spec 2.2)."),
    _fred("be.10y", "T10YIE", "10y breakeven inflation", "rates", "implied", "pct", "diff",
          notes="Contains a liquidity premium; not a clean expectation (spec 2.2)."),
    _fred("be.5y5y", "T5YIFR", "5y5y forward inflation expectation", "rates", "implied",
          "pct", "diff"),
]

# --- Credit ------------------------------------------------------------------
CREDIT = [
    _fred("credit.ig.oas", "BAMLC0A0CM", "ICE BofA US Corporate OAS", "credit", "spread",
          "pct", "diff",
          notes="ICE licensing limits FRED to a TRAILING 3-YEAR WINDOW. History "
                "older than that is unavailable and the early end disappears as "
                "time passes, so this store must archive it as it arrives. "
                "3y percentiles (spec 5) are not computable from FRED alone."),
    _fred("credit.hy.oas", "BAMLH0A0HYM2", "ICE BofA US High Yield OAS", "credit", "spread",
          "pct", "diff",
          notes="Same trailing 3-year window limit as credit.ig.oas."),
]

# --- FX ----------------------------------------------------------------------
FX = [
    _fred("fx.usd.broad", "DTWEXBGS", "Nominal broad USD index", "fx", "level", "index",
          "log_return",
          notes="Publishes with roughly a one-week lag versus the rates series."),
    _fred("fx.eurusd", "DEXUSEU", "EURUSD spot", "fx", "level", "index", "log_return"),
    _fred("fx.usdjpy", "DEXJPUS", "USDJPY spot", "fx", "level", "index", "log_return"),
    _fred("fx.usdcny", "DEXCHUS", "USDCNY spot (onshore)", "fx", "level", "index",
          "log_return",
          notes="This is ONSHORE CNY, not the CNH the spec asks for. They differ, "
                "most at exactly the moments of interest. See fx.usdcnh."),
]

# --- Equity ------------------------------------------------------------------
EQUITY = [
    _fred("eq.spx", "SP500", "S&P 500", "equity", "level", "index", "log_return",
          vintage_source="derived_lag",
          notes="Two S&P licensing limits. (1) FRED carries only a TRAILING "
                "10-YEAR window; insufficient for multi-year event studies, so "
                "archive as it arrives. (2) ALFRED carries NO vintage history "
                "for it, so as_of is the value date at the 16:00 snapshot, not a "
                "publication vintage. See adapters.fred.NO_ALFRED_CODES."),
    _fred("eq.ndx", "NASDAQ100", "Nasdaq 100", "equity", "level", "index", "log_return"),
    _fred("eq.nikkei", "NIKKEI225", "Nikkei 225", "equity", "level", "index", "log_return",
          notes="Tokyo close, NOT the 16:00 ET snapshot. Never compare its daily "
                "change with a US close without accounting for the offset (spec 7)."),
]

# --- Commodities -------------------------------------------------------------
COMMODITIES = [
    # Spec 3.1 prescribes log returns for prices. These two are the documented
    # exception: a log return is undefined at or below zero, and both series go
    # there. WTI settled at -36.98 on 2020-04-20, a real market event, not bad
    # data. DHHNGSP carries ten 0.00 prints between 2018 and 2020, which are bad
    # data but are stored as received rather than quietly removed. Differencing
    # in dollars is defined for both and loses nothing at these price levels.
    _fred("cmdty.wti", "DCOILWTICO", "WTI crude spot", "commodity", "level", "usd",
          "diff",
          notes="diff, not log_return: settled at -36.98 on 2020-04-20 and a log "
                "return is undefined there. Change is quoted in USD/bbl."),
    _fred("cmdty.brent", "DCOILBRENTEU", "Brent crude spot", "commodity", "level", "usd",
          "log_return",
          notes="Kept on log returns: Brent never went negative. Deliberately "
                "different from cmdty.wti, so a WTI-Brent comparison must convert "
                "rather than assume a shared unit."),
    _fred("cmdty.natgas", "DHHNGSP", "Henry Hub natural gas spot", "commodity", "level",
          "usd", "diff",
          notes="diff, not log_return: ten 0.00 prints between 2018-01-05 and "
                "2020-12-24 make a log return undefined. Those zeros are bad "
                "source data, kept as received; they will show as large moves "
                "rather than being silently dropped."),
    _fred("cmdty.copper", "PCOPPUSDM", "Global copper price", "commodity", "level", "usd",
          "log_return", frequency="m",
          notes="MONTHLY only on FRED. It cannot appear on a daily change board "
                "(spec 3.1); a daily copper series needs the prices adapter."),
]

# --- Macro actuals -----------------------------------------------------------
# Not itemised in spec 1.3, but these are the only revisable series in the
# universe and therefore the only ones that exercise the vintage machinery
# against real revisions. Phase 4 needs them for the surprise indices anyway.
MACRO = [
    _fred("macro.payrolls", "PAYEMS", "Nonfarm payrolls", "macro", "actual", "index",
          "diff", frequency="m", revisable=True),
    _fred("macro.cpi", "CPIAUCSL", "CPI, all items, SA", "macro", "actual", "index",
          "pct_change", frequency="m", revisable=True),
    _fred("macro.core_cpi", "CPILFESL", "CPI less food and energy, SA", "macro", "actual",
          "index", "pct_change", frequency="m", revisable=True),
    _fred("macro.unrate", "UNRATE", "Unemployment rate", "macro", "actual", "pct", "diff",
          frequency="m", revisable=True),
]

# --- Treasury cross-check curve ---------------------------------------------
TREASURY_CURVE = [
    _treasury("ust_cc.3m", "BC_3MONTH", "UST 3m par yield (Treasury)"),
    _treasury("ust_cc.2y", "BC_2YEAR", "UST 2y par yield (Treasury)"),
    _treasury("ust_cc.5y", "BC_5YEAR", "UST 5y par yield (Treasury)"),
    _treasury("ust_cc.10y", "BC_10YEAR", "UST 10y par yield (Treasury)"),
    _treasury("ust_cc.30y", "BC_30YEAR", "UST 30y par yield (Treasury)"),
]

# --- Volatility --------------------------------------------------------------
VOL = [
    _cboe("vol.vix", "VIX", "VIX"),
    _cboe("vol.vix9d", "VIX9D", "VIX9D"),
    _cboe("vol.vix3m", "VIX3M", "VIX3M"),
    _cboe("vol.vix6m", "VIX6M", "VIX6M"),
    _cboe("vol.skew", "SKEW", "Cboe SKEW", notes="Tail pricing (spec 2.2)."),
]

# --- Registered but not fillable in phase 1 ---------------------------------
PENDING = [
    # Policy path, derived from CME ZQ/SR3 in phase 3 (spec 2.1).
    # Needs a paid or non-FRED price source.
    _pending("eq.sx5e", "Euro Stoxx 50", "prices", "equity", "level", "index", "log_return",
             "Not on FRED. T91's prices adapter reads gex.daily_bars and the desk captures "
             "no Euro Stoxx proxy, so this still needs a vendor. Note the European close is "
             "not the 16:00 ET snapshot either -- a proxy would need a lag convention."),
    _pending("fx.usdcnh", "USDCNH spot (offshore)", "prices", "fx", "level", "index",
             "log_return",
             "FRED carries onshore CNY only (fx.usdcny). T91's prices adapter reads "
             "gex.daily_bars and the desk captures no CNH proxy, so this still needs a "
             "vendor."),
    _pending("vol.move", "MOVE index", "vendor", "vol", "implied", "index", "diff",
             "ICE proprietary, not free at any tier the spec contemplates. Spec 1.3 "
             "already hedges this with 'if available'. Unfilled unless licensed."),
]

# --- Derived (spec 1.3, 2.2; recipes in derive.DERIVATIONS) -----------------
DERIVED = [
    _derived("spread.5s30s", "5s30s spread", "rates", "spread", "pct", "diff",
             "source_vintage",
             "ust.30y.nominal minus ust.5y.nominal. Unlike 2s10s there is no "
             "FRED equivalent, so it is computed."),
    _derived("credit.hy_ig.diff", "HY minus IG OAS", "credit", "spread", "pct", "diff",
             "source_vintage",
             "credit.hy.oas minus credit.ig.oas. Tracked separately from HY "
             "alone: a parallel widening is a different signal (spec 2.2). "
             "Inherits both inputs' trailing 3-year FRED window."),
    _derived("vol.vix9d_ratio", "VIX9D / VIX", "vol", "implied", "ratio", "diff",
             "derived_lag",
             "Above 1 is front-end backwardation, a strong regime marker "
             "(spec 2.2). Starts 2011-01-04, where VIX9D begins."),
    _derived("vol.vix3m_ratio", "VIX3M / VIX", "vol", "implied", "ratio", "diff",
             "derived_lag",
             "Below 1 is term-structure backwardation (spec 2.2). Starts "
             "2009-09-18, where VIX3M begins."),
]

# --- Positioning (spec 1.3) -------------------------------------------------
_COT_NOTE = (
    "Weekly, Tuesday-dated, published the following Friday at 15:30 ET. The "
    "three-day information lag is carried in as_of (spec 1.2). Read the "
    "matching .pctile series, not the raw count: contract counts are not "
    "comparable across time as open interest grows (spec 2.2)."
)
POSITIONING = [
    _cftc("pos.cot.ust10y", "043602", "COT net spec, UST 10Y note", _COT_NOTE),
    _cftc("pos.cot.es", "13874A", "COT net spec, E-mini S&P 500", _COT_NOTE),
    _cftc("pos.cot.dxy", "098662", "COT net spec, USD index", _COT_NOTE),
    _cftc("pos.cot.crude", "067651", "COT net spec, WTI crude", _COT_NOTE),
    _cftc("pos.cot.gold", "088691", "COT net spec, gold", _COT_NOTE),
]

_PCTILE_NOTE = (
    "Percentile of the raw net-spec count within its own trailing 156-week "
    "(3-year) range, inclusive of the current reading (spec 2.2). This is the "
    "figure to read; the raw count is kept as its reproducible input."
)
POSITIONING_PERCENTILES = [
    # WEEKLY, like the COT report it is derived from. Labelling it daily would
    # make it eligible for the daily change board and the PCA panel, where a
    # weekly series joined against daily ones collapses the panel to weekly
    # dates. The max_gap_days rule happens to reject it today, but that is
    # incidental protection, not the declaration being right.
    _derived(f"pos.cot.{k}.pctile", f"COT net spec percentile, {label}",
             "macro", "positioning", "index", "diff", "derived_lag", _PCTILE_NOTE,
             frequency="w")
    for k, label in [
        ("ust10y", "UST 10Y note"), ("es", "E-mini S&P 500"),
        ("dxy", "USD index"), ("crude", "WTI crude"), ("gold", "gold"),
    ]
]

# --- Implied policy path (spec 2.1) -----------------------------------------
_PATH_NOTE = (
    "Solved from a ZQ settlement strip by policy.solve_path. CME publishes no "
    "free settlement history and prohibits automated access to its settlements "
    "endpoint, so this series accumulates forward from whenever settlements are "
    "first supplied and CANNOT be backfilled. The probabilities implied by it "
    "are unvalidated against CME FedWatch; see policy.py."
)
POLICY_PATH = [
    m
    for i in range(1, 9)
    for m in (
        _derived(f"policy.ff.meeting_{i}",
                 f"Implied fed funds rate, meeting +{i}",
                 "rates", "implied", "pct", "diff", "derived_lag", _PATH_NOTE,
                 source=POLICY_SOURCE),
        _derived(f"policy.ff.meeting_{i}.chg",
                 f"Implied cumulative change from spot, meeting +{i}",
                 "rates", "implied", "bp", "diff", "derived_lag", _PATH_NOTE,
                 source=POLICY_SOURCE),
    )
]

# --- ETF proxies, read from gex.daily_bars (T91) -----------------------------
# These three were in PENDING from phase 1, blocking five of the fifteen declared graph edges
# between them, for want of a source that does not exist for free: RU2000PR is retired, MSCI
# EM is licensed, and both LBMA gold fixes were withdrawn. The desk holds five years of daily
# bars for their liquid ETF proxies, so that is what fills them. See adapters/prices.py.
PRICES = [
    _prices("eq.rut", "IWM", "Russell 2000 (IWM proxy)", "the Russell 2000",
            "equity", "level", "index", "log_return"),
    _prices("eq.msci_em", "EEM", "MSCI EM (EEM proxy)", "MSCI EM",
            "equity", "level", "index", "log_return"),
    _prices("cmdty.gold", "GLD", "Gold (GLD proxy)", "gold spot",
            "commodity", "level", "usd", "log_return"),
]

UNIVERSE: list[SeriesMeta] = [
    *RATES, *INFLATION, *CREDIT, *FX, *EQUITY, *COMMODITIES, *MACRO,
    *TREASURY_CURVE, *VOL, *POSITIONING, *DERIVED, *POSITIONING_PERCENTILES,
    *POLICY_PATH, *PRICES, *PENDING,
]


def fetchable() -> list[SeriesMeta]:
    """Series an adapter can actually fill.

    A registered series with no `source_code` is not one of them, however fetchable its
    eventual source is. `_pending` sets the code to `""` precisely to say "no code for this
    exists yet", and until T91 that never mattered because no pending series named a source
    that had an adapter. `eq.sx5e` and `fx.usdcnh` have always named "prices" as their
    eventual home; the moment that source gained an adapter they would otherwise have been
    handed to it as the symbol `""`, which reads `gex.daily_bars` for nothing and fails the
    whole source's batch.
    """
    return [s for s in UNIVERSE if s.source in FETCHABLE_SOURCES and s.source_code]


def by_source(source: str) -> list[SeriesMeta]:
    """Every series naming this source, fillable or not -- including codeless ones."""
    return [s for s in UNIVERSE if s.source == source]


def fetchable_by_source(source: str) -> list[SeriesMeta]:
    """The subset of `by_source` an adapter can be asked for. See `fetchable`."""
    return [s for s in by_source(source) if s.source_code]


def series_map(source: str) -> dict[str, str]:
    """source_code -> series_id, the mapping an adapter is constructed with."""
    return {s.source_code: s.series_id for s in fetchable_by_source(source)}


def check_duplicates() -> None:
    """Guard against a copy-paste collision in the lists above."""
    from .errors import DataIntegrityError

    seen: dict[str, int] = {}
    for s in UNIVERSE:
        seen[s.series_id] = seen.get(s.series_id, 0) + 1
    dupes = sorted(k for k, v in seen.items() if v > 1)
    if dupes:
        raise DataIntegrityError(f"duplicate series_id in universe: {dupes}")
