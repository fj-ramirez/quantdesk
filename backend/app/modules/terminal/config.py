"""Terminal settings, read from the shared `app.core.config` (T79).

The standalone repo had its own `pydantic-settings` class with `env_prefix="XA_"`. The prefix
is preserved -- `XA_FRED_API_KEY`, `XA_ZSCORE_WINDOW` and the rest are the same environment
variables they always were -- but the fields now live on the one `Settings` object the whole
application shares, so there is a single place a deployment is configured and no second `.env`
parse with its own idea of the working directory.

**Why this file still exists.** Roughly 6,600 lines of adapters, analytics, brief and graph
code read `settings.zscore_window`, `settings.beta_window`, `settings.fred_api_key` and so on.
Renaming every one of those to `settings.XA_ZSCORE_WINDOW` would be a large, mechanical diff
through the exact code whose behaviour must be shown not to have changed. So this is a facade,
the same move `store/db.py` makes for the engine: the names the module reads are unchanged, and
each one resolves to a field on the shared settings object.

`db_path` is **gone**, not repointed. There is no file any more. Leaving a path-shaped setting
would invite the same second-store fork that the research module's registry refuses -- a value
someone could point at a stray `.duckdb` and get plausible-looking, unshared data out of.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.core.config import settings as _core


@dataclass(frozen=True)
class Settings:
    """The names the ported code reads, resolved from the shared settings object.

    Frozen: these are read in tight loops across the analytics, and a module that could mutate
    the board's z-score window mid-run would make a rendered board unreproducible from its own
    parameters.
    """

    snapshot_tz: str
    snapshot_local_time: str
    fred_api_key: str
    http_timeout_seconds: float
    backfill_start: date
    zscore_window: int
    min_zscore_observations: int
    max_gap_days: int
    vol_percentile_window: int
    vol_extreme_low_pct: float
    vol_extreme_high_pct: float
    stale_warn_days: int
    pca_window: int
    pca_components: int
    pca_min_series: int
    regime_window_days: int
    regime_score_window: int
    regime_min_z: float
    beta_window: int
    corr_history_window: int
    log_level: str

    #: Where the cached FOMC calendar JSON lives. This is the one path the module still
    #: needs: the calendar is a fetched artifact, not a series, so it has no row to live in.
    #: It resolves under `DATA_DIR` -- the directory that used to hold the `.duckdb` file,
    #: so the cache keeps the location it had before the port -- and is derived here rather
    #: than at the two call sites so a deployment has one place to look.
    fomc_calendar_path: Path


def load_settings() -> Settings:
    """Build the module's settings view. Same call signature the ported code already uses."""
    return Settings(
        snapshot_tz=_core.XA_SNAPSHOT_TZ,
        snapshot_local_time=_core.XA_SNAPSHOT_LOCAL_TIME,
        fred_api_key=_core.XA_FRED_API_KEY,
        http_timeout_seconds=_core.XA_HTTP_TIMEOUT_SECONDS,
        backfill_start=date.fromisoformat(_core.XA_BACKFILL_START),
        zscore_window=_core.XA_ZSCORE_WINDOW,
        min_zscore_observations=_core.XA_MIN_ZSCORE_OBSERVATIONS,
        max_gap_days=_core.XA_MAX_GAP_DAYS,
        vol_percentile_window=_core.XA_VOL_PERCENTILE_WINDOW,
        vol_extreme_low_pct=_core.XA_VOL_EXTREME_LOW_PCT,
        vol_extreme_high_pct=_core.XA_VOL_EXTREME_HIGH_PCT,
        stale_warn_days=_core.XA_STALE_WARN_DAYS,
        pca_window=_core.XA_PCA_WINDOW,
        pca_components=_core.XA_PCA_COMPONENTS,
        pca_min_series=_core.XA_PCA_MIN_SERIES,
        regime_window_days=_core.XA_REGIME_WINDOW_DAYS,
        regime_score_window=_core.XA_REGIME_SCORE_WINDOW,
        regime_min_z=_core.XA_REGIME_MIN_Z,
        beta_window=_core.XA_BETA_WINDOW,
        corr_history_window=_core.XA_CORR_HISTORY_WINDOW,
        log_level=_core.XA_LOG_LEVEL,
        fomc_calendar_path=Path(_core.DATA_DIR) / "fomc_calendar.json",
    )
