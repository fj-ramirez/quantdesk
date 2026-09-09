from pydantic_settings import BaseSettings, SettingsConfigDict

#: T42 default daily-bars universe (plans/continuation/00-foundation-daily-bars.md). The five
#: option underlyings are represented by their ETF proxies (SPX -> SPY, etc. is not literal --
#: SPX itself is fetched too, as ^GSPC inside the Yahoo provider, so the regime board can use
#: index bars directly); SPX and ^VIX are appended per the plan's explicit instruction ("Add
#: SPX and ^VIX to the default SCAN_UNIVERSE string above -- T54 needs the latter and the Yahoo
#: provider already serves it"). Kept as one literal string, not a list, so it round-trips
#: through `.env` the same way `SYMBOLS` already does.
_DEFAULT_SCAN_UNIVERSE = (
    "SPY,QQQ,DIA,IWM,RSP,"
    "XLK,XLF,XLE,XLV,XLI,XLY,XLP,XLU,XLB,XLRE,XLC,"
    "SMH,XBI,KRE,XOP,ITB,XHB,XRT,IGV,ARKK,JETS,"
    "GLD,SLV,USO,UNG,DBA,GDX,COPX,"
    "TLT,IEF,HYG,UUP,FXE,FXY,"
    "EEM,EFA,FXI,EWJ,EWZ,EWG,"
    "SPX,^VIX"
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
    # Empty by default (every symbol uses BARS_PROVIDER); format is
    # "provider1:SYM1,SYM2;provider2:SYM3" -- see that module's docstring. T54 is expected to
    # set this to route ^VIX to a Cboe index-history provider.
    BAR_PROVIDER_GROUPS: str = ""

    @property
    def symbols(self) -> list[str]:
        return [s.strip() for s in self.SYMBOLS.split(",") if s.strip()]

    @property
    def scan_universe(self) -> list[str]:
        return [s.strip() for s in self.SCAN_UNIVERSE.split(",") if s.strip()]


settings = Settings()
