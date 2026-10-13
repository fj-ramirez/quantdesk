"""COT positioning extremes as a weekly-horizon signal on the traded CFDs (T156, plans/cot/).

The declaration this module implements is ``plans/cot/study.md``, frozen before the first run:
84 trials, made up of 14 instrument × positioning-series pairs, two directions and three holds.
Every trial goes into the one registry, so the noise ceiling counts it.

The pure core takes plain frames and returns trades, so it can be tested without a network:

* :func:`percentile` -- the trailing-156-report percentile, the same rule as
  `terminal.derive.rolling_percentile`;
* :func:`release_date` -- the day a report became public, conservatively late;
* :func:`signals` -- one row per usable report: release date, crowded side, episode;
* :func:`simulate` -- fixed-hold trades with a 2 × ATR(20) stop, one position at a time.

The CLI around it downloads CFTC data through the terminal's own adapter (so the field mapping
is the one the desk stores) and daily bars from Yahoo. It writes results to ``--out`` and, with
``--record``, writes the trials to the registry::

    uv run python -m app.modules.research.cot_study --out <dir> [--record]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from app.modules.gex.scan.indicators import atr

from .backtest import noise_ceiling
from .events import clustered_mean_se

log = logging.getLogger(__name__)

MARKET = "cfd_cot"

# -- the declaration (plans/cot/study.md) -------------------------------------------------------

PERCENTILE_WEEKS = 156
HIGH, LOW = 90.0, 10.0
HOLDS = (5, 10, 20)
DIRECTIONS = ("contrarian", "follow")
STOP_ATR_MULT = 2.0
ATR_N = 20
SPLIT = dt.date(2018, 1, 1)


@dataclass(frozen=True)
class Instrument:
    cfd: str
    proxy: str
    #: Round-trip spread as a fraction of price (broker data, 2026-10-13).
    spread: float
    #: Swap per calendar night as a fraction of price: (long, short). Negative is a charge.
    swap: tuple[float, float]
    #: Rolls quarterly (the `.fs` CFDs) and needs the cash-index carry adjustment.
    future: bool
    #: Dividend yield q for the carry adjustment; only meaningful when ``future``.
    div_yield: float = 0.0


INSTRUMENTS = {
    "S&P.fs": Instrument("S&P.fs", "^GSPC", 1.15e-4, (0.0, 0.0), True, 0.018),
    "NAS100.fs": Instrument("NAS100.fs", "^NDX", 8.1e-5, (0.0, 0.0), True, 0.008),
    "DJ30.fs": Instrument("DJ30.fs", "^DJI", 7.7e-5, (0.0, 0.0), True, 0.020),
    "XAUUSD": Instrument("XAUUSD", "GLD", 3.8e-5, (-1.48e-4, 9.7e-5), False),
    "EURUSD": Instrument("EURUSD", "EURUSD=X", 6.2e-5, (-5.7e-5, 3.05e-5), False),
}

#: (CFD, positioning source code as the terminal adapter reads it, short name).
PAIRS: list[tuple[str, str, str]] = [
    ("S&P.fs", "13874A", "es.noncomm"),
    ("S&P.fs", "tff:13874A:lev_money", "es.lev_money"),
    ("S&P.fs", "tff:13874A:asset_mgr", "es.asset_mgr"),
    ("NAS100.fs", "209742", "nas.noncomm"),
    ("NAS100.fs", "tff:209742:lev_money", "nas.lev_money"),
    ("NAS100.fs", "tff:209742:asset_mgr", "nas.asset_mgr"),
    ("DJ30.fs", "124603", "dji.noncomm"),
    ("DJ30.fs", "tff:124603:lev_money", "dji.lev_money"),
    ("DJ30.fs", "tff:124603:asset_mgr", "dji.asset_mgr"),
    ("XAUUSD", "088691", "gold.noncomm"),
    ("XAUUSD", "disagg:088691:m_money", "gold.m_money"),
    ("EURUSD", "099741", "eur.noncomm"),
    ("EURUSD", "tff:099741:lev_money", "eur.lev_money"),
    ("EURUSD", "tff:099741:asset_mgr", "eur.asset_mgr"),
]

#: Report dates whose publication a government shutdown delayed by weeks (CFTC's catch-up
#: schedules; plans/cot/README.md). Dropped as signals, never re-dated by guesswork.
SHUTDOWN_REPORT_WINDOWS = (
    (dt.date(2013, 9, 30), dt.date(2013, 10, 31)),
    (dt.date(2018, 12, 24), dt.date(2019, 3, 1)),
    (dt.date(2025, 9, 30), dt.date(2026, 1, 13)),
)


def trials() -> list[tuple[str, str, str, str, int]]:
    """(cfd, source code, series name, direction, hold) for all 84 declared trials."""
    return [(c, code, name, d, h) for c, code, name in PAIRS for d in DIRECTIONS for h in HOLDS]


# -- pure core ----------------------------------------------------------------------------------


def percentile(net: pd.Series, window: int = PERCENTILE_WEEKS) -> pd.Series:
    """Where each report sits in its trailing ``window`` reports, inclusive, as 0-100."""
    return net.rolling(window, min_periods=window).apply(
        lambda w: 100.0 * (w <= w[-1]).sum() / len(w), raw=True
    )


_HOLIDAYS = {d.date() for d in USFederalHolidayCalendar().holidays("1995-01-01", "2035-12-31")}


def _no_release_on(d: dt.date) -> bool:
    """CFTC does not publish on weekends, federal holidays or the day after Thanksgiving."""
    if d.weekday() >= 5 or d in _HOLIDAYS:
        return True
    return d.month == 11 and d.weekday() == 4 and (d - dt.timedelta(days=1)) in _HOLIDAYS


def release_date(nominal: dt.date) -> dt.date:
    """The adapter's Friday, pushed past any day CFTC could not have published on."""
    d = nominal
    while _no_release_on(d):
        d += dt.timedelta(days=1)
    return d


def in_shutdown(report_date: dt.date) -> bool:
    return any(lo <= report_date <= hi for lo, hi in SHUTDOWN_REPORT_WINDOWS)


def signals(reports: pd.DataFrame) -> pd.DataFrame:
    """Usable signals from a frame of ``value_date``, ``release`` (the adapter's nominal release
    date) and ``net``, ascending.

    Returns ``release`` (corrected), ``crowd`` (+1 crowded long, -1 crowded short) and
    ``episode``: consecutive reports on the same extreme side share one. Episodes are numbered
    over every report, *including* the shutdown-delayed ones, so dropping those does not split
    an episode in two. A report inside a shutdown window is then dropped.
    """
    df = reports.sort_values("value_date").reset_index(drop=True).copy()
    df["pct"] = percentile(df["net"])
    crowd = np.where(df["pct"] >= HIGH, 1, np.where(df["pct"] <= LOW, -1, 0))
    df["crowd"] = crowd
    new_ep = (df["crowd"] != df["crowd"].shift()) | (df["crowd"] == 0)
    df["episode"] = new_ep.cumsum()
    df = df[(df["crowd"] != 0) & df["pct"].notna()]
    df = df[~df["value_date"].map(in_shutdown)]
    df["release"] = df["release"].map(release_date)
    return df[["value_date", "release", "crowd", "episode", "pct"]].reset_index(drop=True)


@dataclass
class Trade:
    entry_date: dt.date
    exit_date: dt.date
    side: int
    entry: float
    exit: float
    stop_dist: float
    how: str
    episode: int
    gross_r: float
    carry_r: float
    spread_r: float
    swap_r: float  # signed: negative is a charge
    rolls: int


def roll_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    """The `.fs` roll Mondays: the Monday of the third-Friday week of Mar/Jun/Sep/Dec."""
    out = []
    for y in range(start.year, end.year + 1):
        for m in (3, 6, 9, 12):
            first = dt.date(y, m, 1)
            third_fri = first + dt.timedelta(days=(4 - first.weekday()) % 7 + 14)
            out.append(third_fri - dt.timedelta(days=4))
    return out


def simulate(
    bars: pd.DataFrame, sig: pd.DataFrame, inst: Instrument, direction: str, hold: int,
    rate: pd.Series | None = None,
) -> list[Trade]:
    """Trades for one trial.

    ``bars`` is the proxy's daily frame indexed by date with open/high/low/close. ``rate`` is
    the T-bill yield in percent, indexed like ``bars``, used only for futures carry.
    """
    dates = np.array([d.date() if hasattr(d, "date") else d for d in bars.index])
    o, h, lo, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = atr(bars, ATR_N).to_numpy(float)
    rolls = roll_dates(dates[0], dates[-1]) if inst.future else []
    rates = rate.reindex(bars.index).ffill().to_numpy(float) if rate is not None else None
    sign = -1 if direction == "contrarian" else 1

    out: list[Trade] = []
    busy_until = -1
    for s in sig.itertuples():
        e = int(np.searchsorted(dates, s.release, side="right"))  # strictly after release
        if e <= busy_until or e + hold >= len(dates) or math.isnan(a[e]) or a[e] <= 0:
            continue
        side = sign * int(s.crowd)
        entry = c[e]
        dist = STOP_ATR_MULT * a[e]
        stop = entry - side * dist
        x, px, how = e + hold, c[e + hold], "time"
        for j in range(e + 1, e + hold + 1):
            if side > 0 and o[j] <= stop:
                x, px, how = j, o[j], "gap"
                break
            if side < 0 and o[j] >= stop:
                x, px, how = j, o[j], "gap"
                break
            if (side > 0 and lo[j] <= stop) or (side < 0 and h[j] >= stop):
                x, px, how = j, stop, "stop"
                break
        d0, d1 = dates[e], dates[x]
        nights = (d1 - d0).days
        carry = 0.0
        if inst.future and rates is not None and not math.isnan(rates[e]):
            carry = -side * (rates[e] / 100.0 - inst.div_yield) * nights / 365.0 * entry
        n_rolls = sum(1 for r in rolls if d0 < r <= d1)
        spread = inst.spread * entry * (1 + n_rolls)
        swap = (inst.swap[0] if side > 0 else inst.swap[1]) * entry * nights
        busy_until = x
        out.append(Trade(
            d0, d1, side, float(entry), float(px), float(dist), how, int(s.episode),
            gross_r=float(side * (px - entry) / dist), carry_r=float(carry / dist),
            spread_r=float(spread / dist), swap_r=float(swap / dist), rolls=n_rolls,
        ))
    return out


def net_r(t: Trade, cost_mult: float) -> float:
    """R after costs. ``cost_mult`` scales the spread and any swap *charge*; a swap credit is
    taken once, never doubled (the conservative reading of "doubles the spread and the swap")."""
    swap = t.swap_r * cost_mult if t.swap_r < 0 else t.swap_r
    return t.gross_r + t.carry_r - cost_mult * t.spread_r + swap


# -- evaluation ---------------------------------------------------------------------------------


def _period_metrics(trades: list[Trade], days: np.ndarray, cost_mult: float,
                    lo: dt.date | None, hi: dt.date | None) -> dict:
    """EdgeLab-shaped metrics on a daily R series (zeros on days with no exit), with R booked
    on the exit day. CAGR and drawdown assume 1 % risk per trade, compounded."""
    keep = np.ones(len(days), bool)
    if lo:
        keep &= days >= lo
    if hi:
        keep &= days < hi
    days = days[keep]
    tr = [t for t in trades if (not lo or t.entry_date >= lo) and (not hi or t.entry_date < hi)]
    r = np.array([net_r(t, cost_mult) for t in tr])
    daily = pd.Series(0.0, index=days)
    if tr:
        booked = pd.Series(r, index=[t.exit_date for t in tr]).groupby(level=0).sum()
        daily = daily.add(booked.reindex(days, fill_value=0.0), fill_value=0.0)
    years = (days[-1] - days[0]).days / 365.25 if len(days) > 1 else 0.0
    per_year = len(days) / years if years > 0 else 0.0
    std = float(daily.std()) if len(daily) > 1 else 0.0
    sharpe = float(daily.mean()) / std * math.sqrt(per_year) if std > 0 else 0.0
    equity = np.cumprod(1.0 + 0.01 * r) if r.size else np.array([1.0])
    peak = np.maximum.accumulate(equity)
    held = sum((t.exit_date - t.entry_date).days for t in tr)
    span = (days[-1] - days[0]).days if len(days) > 1 else 0
    return {
        "sharpe": sharpe,
        "cagr": float(equity[-1] ** (1 / years) - 1) if years > 0 and equity[-1] > 0 else -1.0,
        "max_drawdown": float((equity / peak - 1).min()),
        "n_fills": len(tr),
        "exposure": held / span if span else 0.0,
        "n_bars": len(days),
        "years": years,
    }


def evaluate(trades: list[Trade], days: np.ndarray, n_total: int) -> dict:
    """The six gate criteria of plans/cot/study.md, and the numbers behind them."""
    out: dict = {"n": len(trades)}
    if not trades:
        out["verdict"] = "fail"
        return out
    ep = np.array([t.episode for t in trades])
    out["episodes"] = len(np.unique(ep))
    for k, mult in (("gross", 0.0), ("net1", 1.0), ("net2", 2.0)):
        r = np.array([net_r(t, mult) for t in trades])
        out[f"mean_{k}"], out[f"se_{k}"] = clustered_mean_se(r, ep)
    net2 = np.array([net_r(t, 2.0) for t in trades])
    years = np.array([t.entry_date.year for t in trades])
    yearly = {int(y): float(net2[years == y].mean()) for y in np.unique(years)}
    out["yearly_net2"] = yearly
    out["years_pos"] = sum(1 for v in yearly.values() if v > 0)
    out["years_n"] = len(yearly)
    is_ = np.array([t.entry_date < SPLIT for t in trades])
    out["is_mean_net2"] = float(net2[is_].mean()) if is_.any() else float("nan")
    out["oos_mean_net2"] = float(net2[~is_].mean()) if (~is_).any() else float("nan")
    out["oos_n"] = int((~is_).sum())
    out["is_m"] = _period_metrics(trades, days, 1.0, None, SPLIT)
    out["oos_m"] = _period_metrics(trades, days, 1.0, SPLIT, None)
    out["exits"] = pd.Series([t.how for t in trades]).value_counts().to_dict()
    out["mean_rolls"] = float(np.mean([t.rolls for t in trades]))

    oos_years = out["oos_m"]["years"]
    out["ceiling"] = noise_ceiling(n_total, oos_years)
    out["ceiling_family"] = noise_ceiling(len(trials()), oos_years)
    t_stat = out["mean_net2"] / out["se_net2"] if out["se_net2"] and out["se_net2"] > 0 else 0.0
    out["t_net2"] = t_stat
    g = {
        "g1_count": out["n"] >= 100 and out["episodes"] >= 30,
        "g2_mean": out["mean_net2"] > 0,
        "g3_se": t_stat > 2.0,
        "g4_years": out["years_pos"] > out["years_n"] / 2,
        "g5_regimes": out["is_mean_net2"] > 0 and out["oos_mean_net2"] > 0,
        "g6_ceiling": out["oos_m"]["sharpe"] > out["ceiling"],
    }
    out.update(g)
    if all(g.values()):
        out["verdict"] = "pass"
    elif all(v for k, v in g.items() if k != "g6_ceiling") and t_stat >= 2.0:
        out["verdict"] = "lead"
    else:
        out["verdict"] = "fail"
    return out


# -- data ---------------------------------------------------------------------------------------


def load_positions(cache: Path, refresh: bool = False) -> dict[str, pd.DataFrame]:
    """Every positioning series in :data:`PAIRS`, through the terminal's own adapter."""
    from app.modules.terminal.adapters.cftc import CftcAdapter

    out: dict[str, pd.DataFrame] = {}
    codes = sorted({code for _, code, _ in PAIRS})
    adapter = CftcAdapter({code: code for code in codes})
    try:
        for code in codes:
            path = cache / f"cot_{code.replace(':', '_')}.parquet"
            if path.exists() and not refresh:
                out[code] = pd.read_parquet(path)
                continue
            obs = adapter.fetch([code], dt.date(1995, 1, 1),
                                dt.datetime.now(dt.UTC).date(), "cot_study")
            df = pd.DataFrame({
                "value_date": [o.value_date for o in obs],
                "release": [o.as_of.date() for o in obs],
                "net": [o.value for o in obs],
            })
            df.to_parquet(path)
            out[code] = df
    finally:
        adapter.close()
    return out


def load_bars(cache: Path, symbol: str, refresh: bool = False) -> pd.DataFrame:
    """Unadjusted daily bars from Yahoo, cached. Unadjusted on purpose: the stop and the costs
    are in price units at the time."""
    path = cache / f"yahoo_{symbol.replace('^', '').replace('=', '_')}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    import yfinance as yf

    df = yf.download(symbol, start="1999-01-01", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
    df = df[(df["high"] >= df["low"]) & (df["close"] > 0)]
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    df.to_parquet(path)
    return df


# -- the run ------------------------------------------------------------------------------------


def run(out_dir: Path, n_registry: int, refresh: bool = False) -> pd.DataFrame:
    out_dir.mkdir(parents=True, exist_ok=True)
    positions = load_positions(out_dir, refresh)
    rate = load_bars(out_dir, "^IRX", refresh)["close"]
    n_total = n_registry + len(trials())
    rows = []
    trade_rows = []
    for cfd, code, name, direction, hold in trials():
        inst = INSTRUMENTS[cfd]
        bars = load_bars(out_dir, inst.proxy, refresh)
        sig = signals(positions[code])
        days = np.array([d.date() for d in bars.index])
        trades = simulate(bars, sig, inst, direction, hold, rate if inst.future else None)
        res = evaluate(trades, days, n_total)
        res.update(cfd=cfd, series=name, code=code, direction=direction, hold=hold,
                   signals=len(sig), first_signal=str(sig["release"].min()) if len(sig) else "")
        rows.append(res)
        for t in trades:
            trade_rows.append({**asdict(t), "cfd": cfd, "series": name,
                               "direction": direction, "hold": hold})
        log.info("%-9s %-14s %-10s %2d: n=%3d ep=%3s net2=%+.3f t=%.2f %s", cfd, name,
                 direction, hold, res["n"], res.get("episodes"), res.get("mean_net2", math.nan),
                 res.get("t_net2", math.nan), res["verdict"])
    df = pd.DataFrame(rows)
    df.to_pickle(out_dir / "results.pkl")
    pd.DataFrame(trade_rows).to_parquet(out_dir / "trades.parquet")
    return df


def params_for(direction: str, hold: int) -> dict:
    return {"direction": direction, "hold": hold, "high": HIGH, "low": LOW,
            "window": PERCENTILE_WEEKS, "stop_atr": STOP_ATR_MULT, "cost": "broker_spread_1x",
            "split": SPLIT.isoformat()}


def record(df: pd.DataFrame) -> int:
    """Write every trial to the one registry. IS/OOS metrics are at 1x cost."""
    from .registry import Registry, trial_hash

    reg = Registry()
    today = dt.datetime.now(dt.UTC).date()
    n = 0
    for r in df.itertuples():
        if not isinstance(r.is_m, dict):
            continue
        params = params_for(r.direction, int(r.hold))
        strategy = f"cot_{r.series}"
        h = trial_hash(MARKET, strategy, r.cfd, "1w", params)
        reg.record(h, today, MARKET, strategy, r.cfd, "1w", params, r.is_m, r.oos_m)
        n += 1
    reg.commit()
    reg.close()
    return n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n-registry", type=int, required=True,
                    help="trials already in research.trials (desk_status 'trials searched')")
    ap.add_argument("--refresh", action="store_true", help="refetch CFTC and Yahoo data")
    ap.add_argument("--record", action="store_true", help="write every trial to the registry")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    df = run(args.out, args.n_registry, args.refresh)
    cols = ["cfd", "series", "direction", "hold", "n", "episodes", "mean_net2", "t_net2",
            "years_pos", "years_n", "is_mean_net2", "oos_mean_net2", "verdict"]
    print(df[cols].to_string())
    summary = df["verdict"].value_counts().to_dict()
    print(json.dumps(summary))
    if args.record:
        log.info("recorded %d trials", record(df))


if __name__ == "__main__":
    main()
