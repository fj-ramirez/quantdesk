from pydantic_settings import BaseSettings, SettingsConfigDict

#: T42 default daily-bars universe (plans/continuation/00-foundation-daily-bars.md). The five
#: option underlyings are represented by their ETF proxies (SPX -> SPY, etc. is not literal --
#: SPX itself is fetched too, as ^GSPC inside the Yahoo provider, so the regime board can use
#: index bars directly); SPX and ^VIX are appended per the plan's explicit instruction ("Add
#: SPX and ^VIX to the default SCAN_UNIVERSE string above -- T54 needs the latter and the Yahoo
#: provider already serves it"). T54 adds the other five Cboe index symbols the cross-asset
#: regime strip needs (^VIX9D/^VIX3M/^VIX6M term structure, ^VVIX, ^SKEW) so the daily bars job
#: and `bars_backfill` fetch them the same way as every other symbol -- see
#: `BAR_PROVIDER_GROUPS` below for what routes them to `app.modules.gex.providers.cboe_index` instead of
#: the `^VIX`-shaped default provider. Kept as one literal string, not a list, so it
#: round-trips through `.env` the same way `SYMBOLS` already does.
_DEFAULT_SCAN_UNIVERSE = (
    "SPY,QQQ,DIA,IWM,RSP,"
    "XLK,XLF,XLE,XLV,XLI,XLY,XLP,XLU,XLB,XLRE,XLC,"
    "SMH,XBI,KRE,XOP,ITB,XHB,XRT,IGV,ARKK,JETS,"
    "GLD,SLV,USO,UNG,DBA,GDX,COPX,"
    "TLT,IEF,HYG,UUP,FXE,FXY,"
    "EEM,EFA,FXI,EWJ,EWZ,EWG,"
    "SPX,^VIX,^VIX9D,^VIX3M,^VIX6M,^VVIX,^SKEW,"
    # Single names, added 2026-09-10 on the user's decision (the open "universe width" question
    # in plans/continuation/README.md). The initiative exists because the user's *ETFs* keep
    # fading their breakouts -- and an ETF is a basket, which averages away exactly the
    # continuation this app is looking for. These are liquid, optionable US large/mid caps
    # chosen to span every sector plus the high-beta names where continuation actually shows up.
    #
    # This is a starting set, not a fixed one: the natural source is the user's own broker CFD
    # list, and swapping it in is a change to this one string (or a SCAN_UNIVERSE override in
    # .env) with no code change anywhere. Cost is linear -- the 17:30 bars job and the backfill
    # sleep 0.5 s between symbols, so ~125 symbols is about a minute of wall clock -- and the
    # scan pages all sort and truncate, so a wider universe costs ranking depth, not legibility.
    "AAPL,MSFT,NVDA,GOOGL,META,AMZN,TSLA,AVGO,AMD,MU,QCOM,INTC,ORCL,CRM,ADBE,NFLX,"
    "SMCI,ARM,LRCX,AMAT,TXN,PLTR,"
    "NOW,SNOW,CRWD,PANW,NET,SHOP,UBER,"
    "JPM,BAC,WFC,GS,MS,C,SCHW,AXP,V,MA,"
    "UNH,LLY,JNJ,PFE,MRK,ABBV,AMGN,ISRG,"
    "XOM,CVX,COP,SLB,OXY,FCX,NEM,"
    "BA,CAT,DE,GE,RTX,UPS,FDX,"
    "HD,COST,WMT,NKE,SBUX,MCD,DIS,"
    "COIN,HOOD,DKNG,RIVN,MSTR"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg://gex:gex@localhost:5432/gex"
    DATA_DIR: str = "./data"
    # Continuous dividend yield used to build the option forward in app/modules/gex/gex/greeks.py
    # (forward = spot * exp((RISK_FREE_RATE - DIVIDEND_YIELD) * T)). One global default,
    # roughly the S&P 500 trailing yield; override per call for a symbol that differs.
    DIVIDEND_YIELD: float = 0.013
    # Empty by default so a user running on the default "cboe" provider is never blocked by a
    # missing credential; app.modules.gex.providers.marketdata.MarketDataProvider raises a clear,
    # setting-named error if it is ever constructed without this populated.
    MARKETDATA_TOKEN: str = ""
    PROVIDER: str = "cboe"
    # --- T76: the read-only role's password ---------------------------------------------------
    # Empty by default, same rule as MARKETDATA_TOKEN and TIINGO_TOKEN above: a dev stack that
    # never asked for a read-only connection is not blocked by a missing credential. When it is
    # set, `app.core.ro_role` (run after `alembic upgrade head`) gives `quantdesk_ro` LOGIN and
    # this password, which is what the T82 MCP connector authenticates with.
    #
    # Deliberately not part of DATABASE_URL: that URL is the *application* user, which writes.
    # Rotating this is an edit here plus a restart -- the migration owns the role's privileges,
    # never its secret.
    QUANTDESK_RO_PASSWORD: str = ""

    # --- T77: the research (EdgeLab) search worker ---------------------------------------------
    # The trigger shape for `research-search`, which replaces two schedules the standalone repo
    # had: a Windows Task Scheduler entry at 02:00 and a systemd unit running `--loop 60`.
    #
    # `cron` (the default) reproduces the 02:00 nightly habit; `interval` reproduces the VPS
    # loop; `off` is for a host that only ever runs cycles by hand. See
    # `app/modules/research/jobs/scheduler.py` for the job policies, and note there is
    # deliberately no catch-up: a missed research cycle costs nothing, because the registry
    # already remembers every combination tried.
    RESEARCH_SCHEDULE: str = "cron"
    # A crontab line, read in `settings.TZ`. Written in this form rather than as five separate
    # settings so it round-trips through `.env` as one value and reads the way a crontab does.
    RESEARCH_CRON: str = "0 2 * * *"
    RESEARCH_INTERVAL_MINUTES: int = 60
    # Continuously compounded annualized risk-free rate for Greeks. A parameter, never
    # fetched from a rates feed (PLAN.md / TASKS.md T07).
    RISK_FREE_RATE: float = 0.04
    SYMBOLS: str = "SPX,SPY,QQQ,GLD,DIA"
    TZ: str = "America/New_York"

    # --- T32: retention for intraday `gex_by_strike` detail -----------------------------------
    # How many days of per-strike detail to keep for NON-EOD (intraday) snapshots. EOD strike
    # detail is kept forever, as is every `gex_levels` summary row, every `snapshots` index row
    # and every Parquet file -- see `app.modules.gex.jobs.retention` for the full policy and why pruning is
    # reversible.
    #
    # Sized against measured volume: `gex_by_strike` runs ~800 rows per filter per capture, so
    # five symbols x two non-empty filters x T18's 27 captures a session is ~216k rows/day
    # against ~8k/day at one capture. Thirty days caps the intraday share at roughly 6M rows
    # while still covering "scrub back through last month's sessions" (T20).
    #
    # `0` disables pruning entirely, for a user who would rather buy disk than lose detail.
    INTRADAY_STRIKE_RETENTION_DAYS: int = 30

    # --- T18: 15-minute intraday polling -------------------------------------------------------
    # Off by default, and deliberately so. The scheduler's job store is in memory, so jobs fire
    # only while the process is alive, and unlike the EOD capture an intraday slot has **no**
    # recovery path -- the Cboe endpoint serves only "now", so a slot missed while the laptop
    # was closed is gone permanently and silently.
    #
    # The user decided on 2026-09-11 to keep running on the laptop rather than an always-on host
    # (T70, deferred). Leaving this on under that arrangement would accumulate a series whose
    # gaps are invisible in the data itself -- and a gap and a flat stretch look identical in a
    # time series while meaning opposite things. So: switch it on deliberately, on days the
    # machine will be up through the session.
    #
    # When it is on, the cadence sits exactly on the free source's informal limit of one request
    # per symbol per 15 minutes, with no headroom -- which is why a failed slot is skipped and
    # logged rather than retried.
    INTRADAY_ENABLED: bool = False

    # --- T74: intraday bars (plans/continuous-feed/05-intraday-bars.md) -----------------------
    # Its own flag, deliberately not folded into INTRADAY_ENABLED above: that governs
    # option-chain capture against Cboe, this polls Yahoo, and one source being rate-limited or
    # broken must not force the other off.
    INTRADAY_BARS_ENABLED: bool = False
    # Six symbols, chosen with the user 2026-09-11 over the full 125-symbol scan universe: six
    # requests per poll is ~72/hour at a 5-minute cadence, while the universe would be ~1,500 --
    # which is where an unofficial endpoint starts throttling, and losing Yahoo would take the
    # *daily* bars pipeline down with it.
    INTRADAY_BARS_SYMBOLS: str = "SPX,SPY,QQQ,GLD,DIA,^VIX"
    # Interval string passed straight to the vendor. 5m keeps a session at ~80 buckets per
    # symbol (~500 rows/day across all six, ~125k/year) -- three orders of magnitude below what
    # made T32's retention rule necessary, so this table needs no pruning.
    INTRADAY_BARS_INTERVAL: str = "5m"

    # --- T47: sector/industry ETF option capture (plans/continuation/03-regime-board.md) ------
    # Deliberately NOT folded into `SYMBOLS`: the 16:20 EOD job reads `symbols` only, and this
    # setting drives a separate 16:45 ET job (`capture_extended_job`,
    # `app/modules/gex/jobs/scheduler.py`) so twenty-three extra HTTP calls can never sit in front of the
    # P0 capture. Default is every symbol that passed live verification against Cboe on
    # 2026-09-09 (see `app.modules.gex.models.chain.Underlying`) -- none failed, so nothing is held back.
    EXTENDED_SYMBOLS: str = (
        "XLK,XLF,XLE,XLV,XLI,XLY,XLP,XLU,XLB,XLRE,XLC,"
        "IWM,SMH,XBI,KRE,XOP,TLT,HYG,EEM,FXI,SLV,USO,GDX"
    )

    # --- T42: daily bars (plans/continuation/00-foundation-daily-bars.md) ----------------------
    # Default provider: the Yahoo Finance chart endpoint (free, keyless) -- see
    # app.modules.gex.providers.yahoo for the live-verified response shape and failure modes. Stooq was
    # ruled out (dead as a keyless source, see the plan's "Live verification" section) and is
    # not implemented anywhere in this codebase.
    BARS_PROVIDER: str = "yahoo"
    # Empty by default, same rationale as MARKETDATA_TOKEN above: a user on the default "yahoo"
    # bars provider is never blocked by a missing credential. app.modules.gex.providers.tiingo raises a
    # clear, setting-named error if constructed without this populated.
    TIINGO_TOKEN: str = ""
    # The ~80-symbol universe the six continuation-plan scan tools read bars for. Deliberately
    # NOT `SYMBOLS` (T42 brief: "SYMBOLS drives the option capture and must not be reused for
    # the bars universe") -- the two lists serve unrelated purposes and widening one must never
    # silently widen the other.
    SCAN_UNIVERSE: str = _DEFAULT_SCAN_UNIVERSE
    # Symbol -> provider routing overrides, parsed by app.modules.gex.providers.bars.BarProviderRegistry.
    # Format is "provider1:SYM1,SYM2;provider2:SYM3" -- see that module's docstring. T54's
    # default routes the six Cboe volatility/skew indices to app.modules.gex.providers.cboe_index instead
    # of BARS_PROVIDER's default (yahoo) -- see that provider's module docstring for why ^VIX
    # in particular is deliberately moved off Yahoo, and app.modules.gex.providers.bars's own docstring for
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
    def intraday_bars_symbols(self) -> list[str]:
        """T74's intraday-bar universe. Same parse-on-read pattern as `symbols`."""
        return [s.strip() for s in self.INTRADAY_BARS_SYMBOLS.split(",") if s.strip()]

    @property
    def scan_universe(self) -> list[str]:
        return [s.strip() for s in self.SCAN_UNIVERSE.split(",") if s.strip()]


settings = Settings()
