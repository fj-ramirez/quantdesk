from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The repo root, located from this file rather than from the current directory. `DATA_DIR`
#: below is anchored to it.
#:
#: The documented host commands run from `backend/` (CLAUDE.md's command table) while the
#: containers run from `/app`, so a CWD-relative `DATA_DIR="./data"` means `backend/data` in
#: one and `/data` in the other -- silently, with both working. That is not hypothetical: on
#: 2026-09-20 the host-run research cycle was found writing `backend/data/research` while the
#: Docker worker wrote `./data/research`, two diverging parquet caches and two sets of
#: reports, neither of them wrong and nothing going red. Anchoring costs nothing, because
#: compose passes an absolute `/data` and absolute values are returned untouched.
#:
#: `env_file` is deliberately **not** anchored here. Pointing it at the repo root makes a
#: developer's `.env` -- `INTRADAY_ENABLED=true` and friends -- load during `pytest` run from
#: `backend/`, and five scheduler tests assert on exactly those flags. Config that reaches the
#: tests from outside the repo is a worse problem than the one it would solve; a host command
#: that needs a non-default setting passes it on the command line, as the fixture recipes do.
_REPO_ROOT = Path(__file__).resolve().parents[3]

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

    # --- T79: the terminal module (xactx) ------------------------------------------------------
    # Folded in from the standalone repo's own pydantic-settings class, **keeping its `XA_`
    # environment-variable names exactly**: `XA_FRED_API_KEY`, `XA_ZSCORE_WINDOW` and the rest
    # are the same variables they always were, so an existing `.env` still works. The module
    # reads them through `app/modules/terminal/config.py`, which maps them back to the short
    # attribute names its ~6,600 ported lines already use.
    #
    # There is deliberately **no `XA_DB_PATH`**. The DuckDB file is gone, and a path-shaped
    # setting would be an invitation to point something at a stray `.duckdb` and get
    # plausible-looking, unshared data out of it.
    #
    # These are not deployment knobs -- they are the analytical parameters the spec argues for
    # (a z-score window that excludes the change being scored, a minimum sample below which a
    # z-score is "noise wearing a statistic's clothes", a PCA that refuses to run on too few
    # series). They live here so a board is reproducible from its own configuration, and the
    # reasoning for each value is in `plans/quantdesk/03-terminal-module.md` and the xactx spec.

    # FRED is the only source needing a credential; Treasury, Cboe, CFTC and CME are keyless.
    # Empty by default, same rule as MARKETDATA_TOKEN: a dev stack is never blocked by a
    # missing key, and the adapter fails with a setting-named error if it is actually needed.
    XA_FRED_API_KEY: str = ""
    XA_HTTP_TIMEOUT_SECONDS: float = 30.0

    # Spec 7: one snapshot convention, stored in metadata and applied consistently.
    XA_SNAPSHOT_TZ: str = "America/New_York"
    XA_SNAPSHOT_LOCAL_TIME: str = "16:00"

    # 2003-01-02 is where TIPS real yields -- and therefore breakevens -- begin. Earlier data
    # exists for nominals, but no cross-asset window can use it. A string, not a `date`, so it
    # round-trips through `.env` like every other setting here.
    XA_BACKFILL_START: str = "2003-01-02"

    # --- Normalized change board (spec 3.1) ---
    # The trailing window a z-score is measured against, and which always EXCLUDES the change
    # being scored -- otherwise a move partly defines its own normality.
    XA_ZSCORE_WINDOW: int = 250
    # Refuse to report a z computed from fewer past changes than this.
    XA_MIN_ZSCORE_OBSERVATIONS: int = 60
    # Maximum calendar days between two observations for their difference to count as a
    # one-period change. Five covers a long weekend plus a holiday. A larger gap still yields a
    # change but is excluded from the trailing distribution and flagged: a 10-day move is not a
    # sample from the 1-day distribution.
    XA_MAX_GAP_DAYS: int = 5
    # History of the trailing volatility itself, so the board can say whether today's window is
    # unusually compressed -- a z of 2 against a compressed window means something different.
    XA_VOL_PERCENTILE_WINDOW: int = 750
    XA_VOL_EXTREME_LOW_PCT: float = 10.0
    XA_VOL_EXTREME_HIGH_PCT: float = 90.0
    # Warn when a series' newest observation is older than this at the board's as_of. FX on
    # FRED routinely publishes 6 days late, so an unflagged board would silently compare
    # today's yield move with last week's currency move.
    XA_STALE_WARN_DAYS: int = 3

    # --- Factor decomposition (spec 3.2) ---
    XA_PCA_WINDOW: int = 250
    XA_PCA_COMPONENTS: int = 4
    # A factor model over a handful of series describes those series, not a cross-asset system.
    XA_PCA_MIN_SERIES: int = 10

    # --- Regime classification (spec 3.4) ---
    XA_REGIME_WINDOW_DAYS: int = 20
    XA_REGIME_SCORE_WINDOW: int = 250
    # Below this on every leg the classifier returns "quiet" rather than labelling noise.
    XA_REGIME_MIN_Z: float = 0.75

    # --- Transmission graph (spec 4) ---
    XA_BETA_WINDOW: int = 250
    XA_CORR_HISTORY_WINDOW: int = 756

    XA_LOG_LEVEL: str = "INFO"
    # The nightly ingest sequence's schedule, in `settings.TZ`. 03:00 by default: after the US
    # close and after the sources publish, and an hour clear of the research search at 02:00 so
    # two CPU- and network-heavy jobs do not contend on a single-box deployment.
    XA_INGEST_CRON: str = "0 3 * * *"

    # --- T82: the MCP connector -----------------------------------------------------------------
    # The read-only DSN the connector authenticates with, as `quantdesk_ro` (created in T76 with
    # SELECT on all three schemas and nothing else).
    #
    # **Deliberately separate from DATABASE_URL, and deliberately without a fallback.** If this
    # is unset the server refuses to start rather than borrowing the application's credentials:
    # a fallback would produce a connector that works perfectly, answers every question, and is
    # not read-only -- which is invisible until the day it matters. `app/mcp/db.py` additionally
    # proves at startup that the role it connected as cannot write, because pointing this at the
    # app user is a plausible mistake that no amount of reading the setting would catch.
    #
    #     DATABASE_URL_RO=postgresql://quantdesk_ro:<QUANTDESK_RO_PASSWORD>@localhost:5432/gex
    DATABASE_URL_RO: str = ""
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

    @field_validator("DATA_DIR")
    @classmethod
    def _anchor_data_dir(cls, value: str) -> str:
        """Resolve a relative `DATA_DIR` against the repo root, never the current directory.

        An absolute value -- which is what compose passes (`DATA_DIR: /data`) -- is returned
        untouched. See `_REPO_ROOT` for why a CWD-relative default was a trap.
        """
        path = Path(value)
        return str(path if path.is_absolute() else (_REPO_ROOT / path).resolve())


settings = Settings()
