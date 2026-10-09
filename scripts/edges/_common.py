"""Shared helpers for the edge-search scripts (docs/edges/search-log.md, *Loop protocol*).

One convention everywhere, so every ledger row is comparable:

* Prices are the **open of the first M1 bar at or after** an instant (what you could act at), in
  New York wall-clock time. No bar within 10 minutes means no trade.
* **Cost** is twice the recorded spread at entry (one spread per round trip, doubled).
* `.fs` holds spanning a `broker.rolls` timestamp have the roll step (step × price) removed.
* XAUUSD holds crossing 17:00 New York pay **swap** per night from the spec (points × 0.01 per oz),
  three nights on Wednesday's rollover.
* Results are in % of the entry price per trade. The pass bar is t ≥ max(2, sqrt(2 ln N)).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

NY = "America/New_York"
SPLIT = pd.Timestamp("2024-01-01", tz=NY)
#: XAUUSD swap in points per lot per night (broker.symbol_specs, 2026-10-08). 1 point = $0.01/oz.
XAU_SWAP = {1: -61.6, -1: 40.5}
SWAP_TRIPLE_WEEKDAY = 2  # Wednesday (swap_rollover3days = 3 in MT5 counts Sunday = 0)
INDICES = ["S&P.fs", "NAS100.fs", "DJ30.fs"]

_cache: dict = {}


def load(data: str | Path, sym: str) -> pd.DataFrame:
    key = (str(data), sym)
    if key not in _cache:
        df = pd.read_parquet(Path(data) / f"{sym.replace('&', '-').lower()}.parquet")
        df["ny"] = df["ts"].dt.tz_convert(NY)
        df["spread"] = df["spread"] * 0.01
        _cache[key] = df.set_index("ny").sort_index()
    return _cache[key]


def rolls(data: str | Path) -> pd.DataFrame:
    return pd.read_csv(Path(data) / "rolls.csv", parse_dates=["rolled_at"])


def at(df: pd.DataFrame, times: pd.DatetimeIndex):
    """(open, spread, ok) of the first M1 bar at or after each instant."""
    idx = df.index.searchsorted(times)
    ok = idx < len(df)
    idx = np.where(ok, idx, len(df) - 1)
    ok &= (df.index[idx] - times).total_seconds() < 600
    return df["open"].to_numpy()[idx], df["spread"].to_numpy()[idx], ok


def sessions(data, sym: str) -> pd.DatetimeIndex:
    """US sessions (midnight NY) that have a 15:59 bar."""
    df = load(data, sym)
    m = (df.index.hour == 15) & (df.index.minute == 59)
    return pd.DatetimeIndex(sorted(set(df.index[m].normalize())))


def _rollovers(t0, t1) -> np.ndarray:
    """Swap nights between t0 and t1: 17:00 NY crossings, Wednesday counting three."""
    out = np.zeros(len(t0))
    for i, (a, b) in enumerate(zip(t0, t1)):
        d = a.normalize() + pd.Timedelta(hours=17)
        if d < a:
            d += pd.Timedelta(days=1)
        while d < b:
            if d.weekday() < 5:
                out[i] += 3 if d.weekday() == SWAP_TRIPLE_WEEKDAY else 1
            d += pd.Timedelta(days=1)
    return out


def trades(data, sym: str, t0, t1, side) -> pd.DataFrame:
    """Net % per trade for entries at t0, exits at t1, side +1/-1 (scalar or array)."""
    df = load(data, sym)
    t0, t1 = pd.DatetimeIndex(t0), pd.DatetimeIndex(t1)
    side = np.broadcast_to(np.asarray(side, dtype=float), (len(t0),)).copy()
    p0, s0, ok0 = at(df, t0)
    p1, _, ok1 = at(df, t1)
    move = p1 - p0
    if sym.endswith(".fs"):
        r = rolls(data)
        for _, row in r[r.cfd_symbol == sym].iterrows():
            ts = row.rolled_at.tz_convert(NY)
            hit = (t0 <= ts) & (t1 > ts)
            move[hit] -= row.step * p0[hit]
    swap = np.zeros(len(t0))
    if sym == "XAUUSD":
        nights = _rollovers(t0, t1)
        swap = nights * np.where(side > 0, XAU_SWAP[1], XAU_SWAP[-1]) * 0.01
    net = side * move - 2 * s0 + swap
    ok = ok0 & ok1 & (side != 0)
    return pd.DataFrame({"t0": t0, "pct": net / p0 * 100, "gross_pct": side * move / p0 * 100,
                         "cost_pct": 2 * s0 / p0 * 100, "swap_pct": swap / p0 * 100,
                         "usd_per_001": net * (1.0 if sym == "XAUUSD" else _usd_per_point(sym))})[ok]


def _usd_per_point(sym: str) -> float:
    """$ per 1.00 index point for 0.01 lot (broker.symbol_specs contract sizes)."""
    return {"S&P.fs": 0.50, "NAS100.fs": 0.20, "DJ30.fs": 0.05}[sym]


def bar(n_tests: int) -> float:
    return max(2.0, math.sqrt(2 * math.log(n_tests)))


def summarize(name: str, sym: str, tr: pd.DataFrame, n_tests: int) -> dict:
    x = tr["pct"].to_numpy()
    t0 = pd.DatetimeIndex(tr["t0"])
    oos = t0 >= SPLIT

    def tstat(v):
        return float(v.mean() / v.std(ddof=1) * math.sqrt(len(v))) if len(v) > 2 else float("nan")

    yrs = pd.Series(x, index=t0.year).groupby(level=0).sum()
    res = {
        "name": name, "sym": sym, "n": len(x), "sessions": len(set(t0.normalize())),
        "mean": float(x.mean()) if len(x) else float("nan"), "t": tstat(x),
        "is_mean": float(x[~oos].mean()) if (~oos).any() else float("nan"), "is_t": tstat(x[~oos]),
        "oos_mean": float(x[oos].mean()) if oos.any() else float("nan"), "oos_t": tstat(x[oos]),
        "years_pos": int((yrs > 0).sum()), "years_n": len(yrs), "bar": bar(n_tests),
        "gross": float(tr["gross_pct"].mean()) if len(x) else float("nan"),
        "cost": float(tr["cost_pct"].mean()) if len(x) else float("nan"),
        "usd_month": float(tr["usd_per_001"].groupby(t0.to_period("M")).sum().mean()) if len(x) else 0,
    }
    ok = (res["n"] >= 100 and res["sessions"] >= 30 and res["is_mean"] > 0 and res["oos_mean"] > 0
          and res["years_pos"] > res["years_n"] / 2)
    res["verdict"] = "pass" if ok and res["t"] >= res["bar"] else "lead" if ok and res["t"] >= 2 else "fail"
    print(f"{name:34s} {sym:10s} n={res['n']:5d} mean={res['mean']:+.4f}% (gross {res['gross']:+.4f}, "
          f"cost {res['cost']:.4f}) t={res['t']:+.2f} bar={res['bar']:.2f} | IS {res['is_mean']:+.4f}% "
          f"t={res['is_t']:+.2f} OOS {res['oos_mean']:+.4f}% t={res['oos_t']:+.2f} | years+ "
          f"{res['years_pos']}/{res['years_n']} | $/mo@0.01 {res['usd_month']:+.1f} -> {res['verdict']}")
    print("   years:", " ".join(f"{y}:{v:+.1f}" for y, v in yrs.items()))
    return res


# -- position rules (search-log.md, *Position rules*) ----------------------------------------


def daily_closes(data, sym: str) -> pd.DataFrame:
    """16:00 New York price and spread per US session, indexed by session date (midnight NY)."""
    d = sessions(data, sym)
    p, s, ok = at(load(data, sym), d + pd.Timedelta(hours=16))
    return pd.DataFrame({"close": p, "spread": s}, index=d)[ok]


def eval_positions(name: str, sym: str, closes: pd.DataFrame, pos: pd.Series, n_tests: int) -> dict:
    """Score a position series decided at each 16:00 close and held to the next one."""
    c = closes["close"].to_numpy()
    s = closes["spread"].to_numpy()
    idx = closes.index
    p = pos.reindex(idx).fillna(0.0).to_numpy()
    nxt = np.r_[c[1:], np.nan]
    move = nxt - c
    if sym.endswith(".fs"):
        r = rolls(data_dir_of(closes))
        t0 = idx + pd.Timedelta(hours=16)
        t1 = np.r_[t0[1:], t0[-1:]]
        for _, row in r[r.cfd_symbol == sym].iterrows():
            ts = row.rolled_at.tz_convert(NY)
            hit = (t0 <= ts) & (pd.DatetimeIndex(t1) > ts)
            move[hit] -= row.step * c[hit]
    change = np.abs(np.diff(np.r_[0.0, p]))
    swap = np.zeros(len(c))
    if sym == "XAUUSD":
        t0 = idx + pd.Timedelta(hours=16)
        t1 = pd.DatetimeIndex(np.r_[t0[1:], t0[-1:]])
        nights = _rollovers(t0, t1)
        swap = nights * np.where(p > 0, XAU_SWAP[1], np.where(p < 0, XAU_SWAP[-1], 0.0)) * 0.01
    pnl = p * move - change * s + swap
    pct = pd.Series(pnl / c * 100, index=idx).iloc[:-1]
    p_ = p[:-1]
    active = (p_ != 0) | (change[:-1] != 0)
    x = pct[active]
    entries = int(((p_ != 0) & (np.r_[0.0, p_[:-1]] != p_)).sum())

    def tstat(v):
        return float(v.mean() / v.std(ddof=1) * math.sqrt(len(v))) if len(v) > 2 else float("nan")

    def sharpe(v):
        return float(v.mean() / v.std(ddof=1) * math.sqrt(252)) if len(v) > 2 and v.std() > 0 else 0.0

    bh = pd.Series((move[:-1] - np.r_[s[0], np.zeros(len(c) - 2)]) / c[:-1] * 100, index=idx[:-1])
    if sym == "XAUUSD":
        bh = bh + pd.Series(_rollovers(idx[:-1] + pd.Timedelta(hours=16), idx[1:] + pd.Timedelta(hours=16))
                            * XAU_SWAP[1] * 0.01 / c[:-1] * 100, index=idx[:-1])
    oos = x.index >= SPLIT
    yrs = pct.groupby(pct.index.year).sum()
    res = {"name": name, "sym": sym, "days": int(active.sum()), "entries": entries,
           "mean": float(x.mean()), "t": tstat(x), "is_mean": float(x[~oos].mean()),
           "oos_mean": float(x[oos].mean()), "is_t": tstat(x[~oos]), "oos_t": tstat(x[oos]),
           "sharpe": sharpe(pct), "bh_sharpe": sharpe(bh), "years_pos": int((yrs > 0).sum()),
           "years_n": len(yrs), "bar": bar(n_tests), "total": float(pct.sum()), "bh_total": float(bh.sum())}
    ok = (res["days"] >= 100 and entries >= 10 and res["is_mean"] > 0 and res["oos_mean"] > 0
          and res["years_pos"] > res["years_n"] / 2 and res["sharpe"] > res["bh_sharpe"])
    res["verdict"] = "pass" if ok and res["t"] >= res["bar"] else "lead" if ok and res["t"] >= 2 else "fail"
    print(f"{name:30s} {sym:10s} days={res['days']:5d} entries={entries:4d} mean/day={res['mean']:+.4f}% "
          f"t={res['t']:+.2f} bar={res['bar']:.2f} | IS t={res['is_t']:+.2f} OOS t={res['oos_t']:+.2f} | "
          f"Sharpe {res['sharpe']:.2f} vs B&H {res['bh_sharpe']:.2f} | total {res['total']:+.0f}% vs B&H "
          f"{res['bh_total']:+.0f}% | years+ {res['years_pos']}/{res['years_n']} -> {res['verdict']}")
    print("   years:", " ".join(f"{y}:{v:+.1f}" for y, v in yrs.items()))
    return res


_DATA_OF: dict = {}


def data_dir_of(closes: pd.DataFrame):
    return _DATA_OF[id(closes)]


def closes_for(data, sym: str) -> pd.DataFrame:
    c = daily_closes(data, sym)
    _DATA_OF[id(c)] = data
    return c


# -- pass 4 helpers -------------------------------------------------------------------------


def rth_daily(data, sym: str) -> pd.DataFrame:
    """RTH daily bars from M1: open = 09:30 price, close = 16:00 price, high/low over 09:30-16:00.
    Indexed by session date (midnight New York), like `sessions`."""
    df = load(data, sym)
    m = ((df.index.hour > 9) | ((df.index.hour == 9) & (df.index.minute >= 30))) & (df.index.hour < 16)
    rth = df[m]
    g = rth.groupby(rth.index.normalize())
    out = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min()})
    d = sessions(data, sym)
    out = out.reindex(d)
    o, _, ok_o = at(df, d + pd.Timedelta(hours=9, minutes=30))
    c, _, ok_c = at(df, d + pd.Timedelta(hours=16))
    out["open"], out["close"] = np.where(ok_o, o, np.nan), np.where(ok_c, c, np.nan)
    return out.dropna()


def xasset(data, symbol: str) -> pd.Series:
    """Daily close of a cross-asset series from daily_xasset.csv, indexed by naive date."""
    df = pd.read_csv(Path(data) / "daily_xasset.csv", parse_dates=["date"])
    s = df[df["symbol"] == symbol].set_index("date")["close"].sort_index()
    return s
