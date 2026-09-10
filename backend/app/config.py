from pydantic_settings import BaseSettings, SettingsConfigDict

#: T42 default daily-bars universe (plans/continuation/00-foundation-daily-bars.md). The five
#: option underlyings are represented by their ETF proxies (SPX -> SPY, etc. is not literal --
#: SPX itself is fetched too, as ^GSPC inside the Yahoo provider, so the regime board can use
#: index bars directly); SPX and ^VIX are appended per the plan's explicit instruction ("Add
#: SPX and ^VIX to the default SCAN_UNIVERSE string above -- T54 needs the latter and the Yahoo
#: provider already serves it"). T54 adds the other five Cboe index symbols the cross-asset
#: regime strip needs (^VIX9D/^VIX3M/^VIX6M term structure, ^VVIX, ^SKEW) so the daily bars job
#: and `bars_backfill` fetch them the same way as every other symbol -- see
#: `BAR_PROVIDER_GROUPS` below for what routes them to `app.providers.cboe_index` instead of
#: the `^VIX`-shaped default provider. Kept as one literal string, not a list, so it
#: round-trips through `.env` the same way `SYMBOLS` already does.
_DEFAULT_SCAN_UNIVERSE = (
    "SPY,QQQ,DIA,IWM,RSP,"
    "XLK,XLF,XLE,XLV,XLI,XLY,XLP,XLU,XLB,XLRE,XLC,"
    "SMH,XBI,KRE,XOP,ITB,XHB,XRT,IGV,ARKK,JETS,"
    "GLD,SLV,USO,UNG,DBA,GDX,COPX,"
    "TLT,IEF,HYG,UUP,FXE,FXY,"
    "EEM,EFA,FXI,EWJ,EWZ,EWG,"
    "SPX,^VIX,^VIX9D,^VIX3M,^VIX6M,^VVIX,^SKEW"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg://gex:gex@localhost:5432/gex"
    DATA_DIR: str = "./data"
    # Continuous dividend yield used to build the option forward in app/gex/greeks.py
    # (forward = spot * exp((RISK_FREE_RATE - DIVIDEND_YIELD) * T)). One global default,
    # roughly the S&P 500 trailing yield; override per call for a symbol that differs.
    DIVIDEND_YIELD: float = 0.013
    # Empty by default so a user running on the default "cboe" provider is never blocked by a
    # missing credential; app.providers.marketdata.MarketDataProvider raises a clear,
    # setting-named error if it is ever constructed without this populated.
    MARKETDATA_TOKEN: str = ""
    PROVIDER: str = "cboe"
    # Continuously compounded annualized risk-free rate for Greeks. A parameter, never
    # fetched from a rates feed (PLAN.md / TASKS.md T07).
    RISK_FREE_RATE: float = 0.04
    SYMBOLS: str = "SPX,SPY,QQQ,GLD,DIA"
    TZ: str = "America/New_York"

    # --- T47: sector/industry ETF option capture (plans/continuation/03-regime-board.md) ------
    # Deliberately NOT folded into `SYMBOLS`: the 16:20 EOD job reads `symbols` only, and this
    # setting drives a separate 16:45 ET job (`capture_extended_job`,
    # `app/jobs/scheduler.py`) so twenty-three extra HTTP calls can never sit in front of the
    # P0 capture. Default is every symbol that passed live verification against Cboe on
    # 2026-09-09 (see `app.models.chain.Underlying`) -- none failed, so nothing is held back.
    EXTENDED_SYMBOLS: str = (
        "XLK,XLF,XLE,XLV,XLI,XLY,XLP,XLU,XLB,XLRE,XLC,"
        "IWM,SMH,XBI,KRE,XOP,TLT,HYG,EEM,FXI,SLV,USO,GDX"
    )

    # --- T42: daily bars (plans/continuation/00-foundation-daily-bars.md) ----------------------
    # Default provider: the Yahoo Finance chart endpoint (free, keyless) -- see
    # app.providers.yahoo for the live-verified response shape and failure modes. Stooq was
    # ruled out (dead as a keyless source, see the plan's "Live verification" section) and is
    # not implemented anywhere in this codebase.
    BARS_PROVIDER: str = "yahoo"
    # Empty by default, same rationale as MARKETDATA_TOKEN above: a user on the default "yahoo"
    # bars provider is never blocked by a missing credential. app.providers.tiingo raises a
    # clear, setting-named error if constructed without this populated.
    TIINGO_TOKEN: str = ""
    # The ~80-symbol universe the six continuation-plan scan tools read bars for. Deliberately
    # NOT `SYMBOLS` (T42 brief: "SYMBOLS drives the option capture and must not be reused for
    # the bars universe") -- the two lists serve unrelated purposes and widening one must never
    # silently widen the other.
    SCAN_UNIVERSE: str = _DEFAULT_SCAN_UNIVERSE
    # Symbol -> provider routing overrides, parsed by app.providers.bars.BarProviderRegistry.
    # Format is "provider1:SYM1,SYM2;provider2:SYM3" -- see that module's docstring. T54's
    # default routes the six Cboe volatility/skew indices to app.providers.cboe_index instead
    # of BARS_PROVIDER's default (yahoo) -- see that provider's module docstring for why ^VIX
    # in particular is deliberately moved off Yahoo, and app.providers.bars's own docstring for
    # why a group entry always wins over the default.
    BAR_PROVIDER_GROUPS: str = "cboe_index:^VIX,^VIX9D,^VIX3M,^VIX6M,^VVIX,^SKEW"

    @property
    def symbols(self) -> list[str]:
        return [s.strip() for s in self.SYMBOLS.split(",") if s.strip()]

    @property
    def extended_symbols(self) -> list[str]:
        """T47's sector/industry ETF universe, captured separately from `symbols` at 16:45 ET.
        Same parse-on-read pattern as `symbols` so both settings round-trip through `.env`
        identically.
        """
        return [s.strip() for s in self.EXTENDED_SYMBOLS.split(",") if s.strip()]

    @property
    def scan_universe(self) -> list[str]:
        return [s.strip() for s in self.SCAN_UNIVERSE.split(",") if s.strip()]


settings = Settings()
