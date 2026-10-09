"""Price-action event study on the broker's CFD bars (T147, plans/charter-mt5/README.md).

Tests the T146 detectors (`app.modules.gex.scan.price_action`) as **intraday** trades on the
CFDs the user actually trades, scored in R on M1 bars by `events.replay`, with the spread
recorded on each entry bar as the cost, and doubled for the gate.

The whole search space is :data:`SEARCH_SPACE`, declared here before the first run and never
widened after seeing results (decision 9). Every combination is one trial and goes into the one
registry, so the noise ceiling counts it. The split is fixed as well: **in-sample before
2024-01-01, out-of-sample from it on**, plus calendar-year windows for the walk-forward signs.

Fixed choices, named here because each one is a judgement and none is searched:

* **Intraday only.** Entries need the signal bar to close inside :data:`ENTRY_WINDOW`
  (New York time), and every trade is flat by :data:`FLAT_AT`. So no trade pays swap or holds
  across the broker's rollover or a futures roll.
* **Roll sessions are excluded.** The session in which `broker.rolls` places a roll, and the two
  after it, take no entries. Swings from before the roll sit in the wrong contract's price.
* **Structural stops.** The stop goes beyond the signal bar's extreme (both bars' extremes for
  engulfing), and is never closer than :data:`STOP_FLOOR_ATR` × ATR(14) of the signal timeframe.
* **Two exits.** ``2R`` is a stop plus a 2R target with a time exit at ``FLAT_AT``. ``time12`` is
  a stop plus a time exit after 12 signal-timeframe bars (or ``FLAT_AT``, whichever comes first).
* **US2000 is out.** It is a cash CFD with a 0.1-lot minimum. The user trades the futures-based
  ones (memory: trades-futures-based-cfds).

Run (bars exported from `broker.bars` as one Parquet per symbol, see `--bars-dir`)::

    uv run python -m app.modules.research.pa_study --bars-dir <dir> --rolls <rolls.csv> \\
        --out <dir> [--record]
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from app.modules.gex.scan import price_action as pa
from app.modules.gex.scan.indicators import atr

from .events import LONG, SHORT, Intent, Trade, clustered_mean_se, replay

log = logging.getLogger(__name__)

NY = "America/New_York"
MARKET = "cfd_pa"

#: The search space: pattern -> its two variants. 6 x 2 x 4 symbols x 3 timeframes x 2 exits.
SEARCH_SPACE: dict[str, list[dict]] = {
    "sweep": [{"swing": 3}, {"swing": 5}],
    "bos": [{"swing": 3}, {"swing": 5}],
    "choch": [{"swing": 3}, {"swing": 5}],
    "pin": [{"wick": 0.6}, {"wick": 0.7}],
    "engulfing": [{"trend": "any"}, {"trend": "with"}],
    "pd_sweep": [{"pierce_atr": 0.0}, {"pierce_atr": 0.25}],
}
SYMBOLS = ["S&P.fs", "NAS100.fs", "DJ30.fs", "XAUUSD"]
TIMEFRAMES = {"M5": 5, "M15": 15, "H1": 60}
EXITS = ["2R", "time12"]

#: Price per MT5 point. All four symbols quote two decimals (`broker.symbol_specs`).
POINT = 0.01
#: Axi Standard charges no commission on these CFDs. A config value, as decision 5 says.
COMMISSION = 0.0
STOP_FLOOR_ATR = 0.5
TIME_EXIT_BARS = 12
SPLIT = dt.date(2024, 1, 1)
ROLL_EXCLUDE_SESSIONS = 3

#: (start, end) of the signal bar's close, New York wall clock, per symbol.
ENTRY_WINDOW = {
    "S&P.fs": (dt.time(9, 35), dt.time(15, 30)),
    "NAS100.fs": (dt.time(9, 35), dt.time(15, 30)),
    "DJ30.fs": (dt.time(9, 35), dt.time(15, 30)),
    "XAUUSD": (dt.time(3, 0), dt.time(15, 30)),
}
FLAT_AT = {"S&P.fs": dt.time(15, 55), "NAS100.fs": dt.time(15, 55), "DJ30.fs": dt.time(15, 55),
           "XAUUSD": dt.time(16, 0)}


def slug(symbol: str) -> str:
    return symbol.replace("&", "-").lower()


# -- data -------------------------------------------------------------------------------------


@dataclass
class M1:
    symbol: str
    ts: pd.DatetimeIndex  # UTC, bar open
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    spread: np.ndarray  # price units
    session: np.ndarray  # NY trading-session date per bar (18:00 NY starts the next session)


def session_dates(ts: pd.DatetimeIndex) -> np.ndarray:
    """The trading session each instant belongs to: 18:00 New York opens the next day's."""
    return (ts.tz_convert(NY) + pd.Timedelta(hours=6)).date


def load_m1(bars_dir: Path, symbol: str) -> M1:
    df = pd.read_parquet(bars_dir / f"{slug(symbol)}.parquet").sort_values("ts")
    ts = pd.DatetimeIndex(df["ts"])
    return M1(
        symbol,
        ts,
        df["open"].to_numpy(float),
        df["high"].to_numpy(float),
        df["low"].to_numpy(float),
        df["close"].to_numpy(float),
        df["spread"].to_numpy(float) * POINT,
        session_dates(ts),
    )


def resample(m1: M1, minutes: int) -> pd.DataFrame:
    """Signal-timeframe bars from M1. ``end`` is the instant the bar is complete."""
    df = pd.DataFrame(
        {"open": m1.open, "high": m1.high, "low": m1.low, "close": m1.close}, index=m1.ts
    )
    if minutes == 1:
        out = df.copy()
    else:
        out = df.resample(f"{minutes}min", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}
        ).dropna()
    out["end"] = out.index + pd.Timedelta(minutes=minutes)
    return out.reset_index(names="start")


def roll_sessions(rolls: pd.DataFrame, symbol: str, sessions: np.ndarray) -> set:
    """Sessions excluded around each roll: the roll's own and the next ones."""
    uniq = np.unique(sessions)
    out: set = set()
    for ts in pd.to_datetime(rolls.loc[rolls["cfd_symbol"] == symbol, "rolled_at"], utc=True):
        d = session_dates(pd.DatetimeIndex([ts]))[0]
        i = int(np.searchsorted(uniq, d))
        out.update(uniq[i : i + ROLL_EXCLUDE_SESSIONS])
    return out


# -- signals ----------------------------------------------------------------------------------


def _side(direction: str) -> int:
    return LONG if direction == pa.UP else SHORT


def raw_signals(bars: pd.DataFrame, pattern: str, variant: dict) -> list[tuple[int, int, float]]:
    """``(bar position, side, structural stop)`` for every event of ``pattern``, actionable at
    the close of ``bar position`` (the event's ``confirmed``)."""
    hi, lo = bars["high"].to_numpy(float), bars["low"].to_numpy(float)
    out: list[tuple[int, int, float]] = []

    def stop_for(t: int, side: int, two_bars: bool = False) -> float:
        a = t - 1 if two_bars and t > 0 else t
        return float(lo[a : t + 1].min()) if side == LONG else float(hi[a : t + 1].max())

    if pattern in ("sweep", "bos", "choch"):
        sw = pa.swings(bars, variant["swing"], variant["swing"])
        events = pa.sweeps(bars, sw) if pattern == "sweep" else [
            e for e in pa.breaks(bars, sw) if e.kind == pattern
        ]
        for e in events:
            side = _side(e.direction)
            out.append((e.confirmed, side, stop_for(e.confirmed, side)))
    elif pattern == "pin":
        for e in pa.bar_patterns(bars, pin_wick_min=variant["wick"]):
            if e.kind == "pin":
                side = _side(e.direction)
                out.append((e.confirmed, side, stop_for(e.confirmed, side)))
    elif pattern == "engulfing":
        trend = None
        if variant["trend"] == "with":
            trend = np.zeros(len(bars), dtype=int)
            for e in pa.breaks(bars, pa.swings(bars)):
                trend[e.confirmed:] = _side(e.direction)
        for e in pa.bar_patterns(bars):
            if e.kind != "engulfing":
                continue
            side = _side(e.direction)
            if trend is not None and trend[e.confirmed] != side:
                continue
            out.append((e.confirmed, side, stop_for(e.confirmed, side, two_bars=True)))
    elif pattern == "pd_sweep":
        out = _pd_sweeps(bars, variant["pierce_atr"])
    else:
        raise ValueError(f"unknown pattern {pattern}")
    return out


def _pd_sweeps(bars: pd.DataFrame, pierce_atr: float) -> list[tuple[int, int, float]]:
    """A wick through the previous session's high (low) that closes back inside: short (long).
    The first per side per session only. ``pierce_atr`` is how far past the level, in ATR(14)
    of the signal timeframe, the wick must reach."""
    sess = session_dates(pd.DatetimeIndex(bars["start"]))
    hi, lo, cl = (bars[c].to_numpy(float) for c in ("high", "low", "close"))
    a = atr(bars, 14).to_numpy(float)
    by = pd.DataFrame({"s": sess, "h": hi, "l": lo}).groupby("s").agg(h=("h", "max"), l=("l", "min"))
    prev = by.shift(1)
    pdh = prev["h"].reindex(sess).to_numpy(float)
    pdl = prev["l"].reindex(sess).to_numpy(float)
    out: list[tuple[int, int, float]] = []
    done: set = set()
    for t in range(len(bars)):
        if math.isnan(pdh[t]) or math.isnan(a[t]):
            continue
        need = pierce_atr * a[t]
        if (sess[t], SHORT) not in done and hi[t] > pdh[t] + need and cl[t] < pdh[t]:
            out.append((t, SHORT, float(hi[t])))
            done.add((sess[t], SHORT))
        if (sess[t], LONG) not in done and lo[t] < pdl[t] - need and cl[t] > pdl[t]:
            out.append((t, LONG, float(lo[t])))
            done.add((sess[t], LONG))
    return out


def intents(
    m1: M1, bars: pd.DataFrame, sigs: list[tuple[int, int, float]], minutes: int, exit_: str,
    excluded: set,
) -> list[Intent]:
    """Turn signals into fill-ready intents: window, roll and ATR filters, stop floor, exits."""
    a = atr(bars, 14).to_numpy(float)
    end = pd.DatetimeIndex(bars["end"])
    end_ny = end.tz_convert(NY)
    sess = session_dates(end)
    w0, w1 = ENTRY_WINDOW[m1.symbol]
    flat = FLAT_AT[m1.symbol]
    out: list[Intent] = []
    for t, side, stop in sigs:
        if math.isnan(a[t]) or sess[t] in excluded:
            continue
        clock = end_ny[t].time()
        if not (w0 <= clock < w1):
            continue
        fill = int(np.searchsorted(m1.ts, end[t]))
        if fill >= len(m1.ts) or m1.session[fill] != sess[t]:
            continue
        flat_ts = pd.Timestamp.combine(end_ny[t].date(), flat).tz_localize(NY).tz_convert("UTC")
        deadline = int(np.searchsorted(m1.ts, flat_ts))
        if exit_ == "time12":
            deadline = min(deadline, int(np.searchsorted(
                m1.ts, end[t] + pd.Timedelta(minutes=minutes * TIME_EXIT_BARS))))
        entry = m1.open[fill]
        floor = STOP_FLOOR_ATR * a[t]
        if (entry - stop) * side < floor:
            stop = entry - side * floor
        risk = (entry - stop) * side
        target = entry + side * 2 * risk if exit_ == "2R" else None
        out.append(Intent(fill, side, float(stop), target, deadline))
    return out


# -- metrics ----------------------------------------------------------------------------------


def _period_metrics(m1: M1, trades: list[Trade], cost_mult: float, lo: dt.date | None,
                    hi: dt.date | None) -> dict:
    """EdgeLab-shaped metrics for the trades whose fill falls in ``[lo, hi)``, on a daily R
    series that includes no-trade sessions as zero. CAGR and drawdown assume 1% risk per
    trade, compounded, so they are comparable across symbols."""
    days = np.unique(m1.session)
    keep_days = np.ones(len(days), bool)
    if lo:
        keep_days &= days >= lo
    if hi:
        keep_days &= days < hi
    days = days[keep_days]
    tr = [t for t in trades if (not lo or m1.session[t.fill] >= lo)
          and (not hi or m1.session[t.fill] < hi)]
    r = np.array([t.gross_r - cost_mult * t.cost_r for t in tr])
    daily = pd.Series(0.0, index=days)
    if tr:
        daily = daily.add(pd.Series(r, index=[m1.session[t.fill] for t in tr]).groupby(level=0)
                          .sum(), fill_value=0.0)
    years = (days[-1] - days[0]).days / 365.25 if len(days) > 1 else 0.0
    per_year = len(days) / years if years > 0 else 0.0
    std = float(daily.std()) if len(daily) > 1 else 0.0
    sharpe = float(daily.mean()) / std * math.sqrt(per_year) if std > 0 else 0.0
    equity = np.cumprod(1.0 + 0.01 * r) if r.size else np.array([1.0])
    peak = np.maximum.accumulate(equity)
    held = sum(t.exit - t.fill + 1 for t in tr)
    bar_mask = np.ones(len(m1.session), bool)
    if lo:
        bar_mask &= m1.session >= lo
    if hi:
        bar_mask &= m1.session < hi
    in_period = int(bar_mask.sum())
    return {
        "sharpe": sharpe,
        "cagr": float(equity[-1] ** (1 / years) - 1) if years > 0 and equity[-1] > 0 else -1.0,
        "max_drawdown": float((equity / peak - 1).min()),
        "n_fills": len(tr),
        "exposure": held / in_period if in_period else 0.0,
        "n_bars": len(days),
        "years": years,
    }


def evaluate(m1: M1, trades: list[Trade]) -> dict:
    """The study's own report for one trial: R statistics at 0x/1x/2x cost, clustered by
    session, yearly signs at 2x cost, and the IS/OOS EdgeLab metrics at 1x cost."""
    out: dict = {"n": len(trades)}
    if not trades:
        return out
    gross = np.array([t.gross_r for t in trades])
    cost = np.array([t.cost_r for t in trades])
    sess = np.array([m1.session[t.fill] for t in trades])
    out["sessions"] = len(np.unique(sess))
    for k, mult in (("gross", 0), ("net1", 1), ("net2", 2)):
        mean, se = clustered_mean_se(gross - mult * cost, sess)
        out[f"mean_{k}"], out[f"se_{k}"] = mean, se
    out["cost_share"] = float(cost.mean() / gross.mean()) if gross.mean() > 0 else float("inf")
    out["median_cost_r"] = float(np.median(cost))
    net2 = gross - 2 * cost
    years = np.array([d.year for d in sess])
    yearly = {int(y): float(net2[years == y].mean()) for y in np.unique(years)}
    out["yearly_net2"] = yearly
    out["years_pos"] = sum(1 for v in yearly.values() if v > 0)
    out["years_n"] = len(yearly)
    oos = sess >= SPLIT
    if oos.any():
        m, s = clustered_mean_se(net2[oos], sess[oos])
        out["oos_n"], out["oos_mean_net2"], out["oos_se_net2"] = int(oos.sum()), m, s
    out["is_m"] = _period_metrics(m1, trades, 1, None, SPLIT)
    out["oos_m"] = _period_metrics(m1, trades, 1, SPLIT, None)
    out["oos_m2"] = _period_metrics(m1, trades, 2, SPLIT, None)
    hows = pd.Series([t.how for t in trades]).value_counts().to_dict()
    out["exits"] = {k: int(v) for k, v in hows.items()}
    return out


# -- the run ----------------------------------------------------------------------------------


def trials() -> list[tuple[str, dict, str, str, str]]:
    return [
        (p, v, s, tf, x)
        for p, vs in SEARCH_SPACE.items()
        for v in vs
        for s, tf, x in itertools.product(SYMBOLS, TIMEFRAMES, EXITS)
    ]


def params_for(variant: dict, exit_: str) -> dict:
    return {**variant, "exit": exit_, "cost": "recorded_spread_1x", "split": SPLIT.isoformat(),
            "session": "intraday"}


def run(bars_dir: Path, rolls_csv: Path, out_dir: Path) -> pd.DataFrame:
    rolls = pd.read_csv(rolls_csv)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for symbol in SYMBOLS:
        m1 = load_m1(bars_dir, symbol)
        excluded = roll_sessions(rolls, symbol, m1.session)
        log.info("%s: %d M1 bars, %d roll-excluded sessions", symbol, len(m1.ts), len(excluded))
        for tf, minutes in TIMEFRAMES.items():
            bars = resample(m1, minutes)
            for pattern, variants in SEARCH_SPACE.items():
                for variant in variants:
                    sigs = raw_signals(bars, pattern, variant)
                    for exit_ in EXITS:
                        its = intents(m1, bars, sigs, minutes, exit_, excluded)
                        trades, skipped = replay(m1.open, m1.high, m1.low, m1.close, m1.spread,
                                                 its, commission=COMMISSION)
                        res = evaluate(m1, trades)
                        res.update(pattern=pattern, variant=json.dumps(variant), symbol=symbol,
                                   timeframe=tf, exit=exit_, signals=len(sigs),
                                   intents=len(its), skipped=json.dumps(skipped))
                        rows.append(res)
                        log.info("%s %s %s %s %s: n=%d net2=%.3f", symbol, tf, pattern,
                                 variant, exit_, res["n"], res.get("mean_net2", float("nan")))
                        if trades:
                            fills = [t.fill for t in trades]
                            pd.DataFrame([asdict(t) for t in trades]).assign(
                                ts=m1.ts[fills], session=m1.session[fills],
                            ).to_parquet(out_dir / f"trades_{slug(symbol)}_{tf}_{pattern}_"
                                         f"{'_'.join(f'{k}{v}' for k, v in variant.items())}"
                                         f"_{exit_}.parquet")
    df = pd.DataFrame(rows)
    df.to_pickle(out_dir / "results.pkl")
    return df


def record(df: pd.DataFrame) -> int:
    """Write every trial to the one registry. Each row's IS/OOS metrics are at 1x cost."""
    from .registry import Registry, trial_hash

    reg = Registry()
    today = dt.datetime.now(dt.UTC).date()
    n = 0
    for r in df.itertuples():
        if not isinstance(r.is_m, dict):
            continue
        params = params_for(json.loads(r.variant), r.exit)
        h = trial_hash(MARKET, f"pa_{r.pattern}", r.symbol, r.timeframe, params)
        reg.record(h, today, MARKET, f"pa_{r.pattern}", r.symbol, r.timeframe, params,
                   r.is_m, r.oos_m)
        n += 1
    reg.commit()
    reg.close()
    return n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bars-dir", type=Path, required=True)
    ap.add_argument("--rolls", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--record", action="store_true", help="write every trial to the registry")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    df = run(args.bars_dir, args.rolls, args.out)
    if args.record:
        log.info("recorded %d trials", record(df))


if __name__ == "__main__":
    main()
