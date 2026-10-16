"""Helpers for per-trade tests on Yahoo daily history before the broker span (2026-10-14).

The broker's M1 history starts 2019-07, so event effects with a handful of trades a year (FOMC,
holidays) cannot reach 100 trades there. These tests run on ^GSPC / ^NDX daily closes up to
2019-06-30, which no broker-span test has touched, at the matching CFD's median spread, doubled.
With no 2024 split on this span, IS/OOS reads as the two halves of the sample (the #356-357
convention).
"""

from __future__ import annotations

import math

import pandas as pd
import yfinance as yf
from _common import bar, closes_for

PAIRS = [("^GSPC", "S&P.fs"), ("^NDX", "NAS100.fs")]


def yahoo_closes(ticker: str, start: str, end: str = "2019-07-01") -> pd.Series:
    raw = yf.download(ticker, start=start, end=end, auto_adjust=False, progress=False)
    c = raw["Close"].squeeze().dropna()
    c.index = pd.DatetimeIndex(c.index).normalize()
    return c


def spread_pct(data, cfd: str) -> float:
    cc = closes_for(data, cfd)
    return float((cc["spread"] / cc["close"]).median())


def close_to_close(c: pd.Series, entries: list, exits: list, cost_pct: float) -> pd.DataFrame:
    """Long from the close of each entry date to the close of the paired exit date, in %."""
    e, x = pd.DatetimeIndex(entries), pd.DatetimeIndex(exits)
    ok = e.isin(c.index) & x.isin(c.index) & (x > e)
    e, x = e[ok], x[ok]
    gross = (c.loc[x].to_numpy() / c.loc[e].to_numpy() - 1) * 100
    return pd.DataFrame({"t0": e, "gross": gross, "pct": gross - 2 * cost_pct * 100})


def summarize(name: str, ticker: str, tr: pd.DataFrame, n_tests: int) -> dict:
    x = tr["pct"].to_numpy()
    t0 = pd.DatetimeIndex(tr["t0"])

    def tstat(v):
        return float(v.mean() / v.std(ddof=1) * math.sqrt(len(v))) if len(v) > 2 else float("nan")

    half = t0 >= t0[len(t0) // 2]
    yrs = pd.Series(x, index=t0.year).groupby(level=0).sum()
    res = {"name": name, "sym": ticker, "n": len(x), "mean": float(x.mean()), "t": tstat(x),
           "gross": float(tr["gross"].mean()), "h1": float(x[~half].mean()), "h1_t": tstat(x[~half]),
           "h2": float(x[half].mean()), "h2_t": tstat(x[half]), "split": str(t0[len(t0) // 2].date()),
           "years_pos": int((yrs > 0).sum()), "years_n": len(yrs), "bar": bar(n_tests)}
    ok = res["n"] >= 100 and res["h1"] > 0 and res["h2"] > 0 and res["years_pos"] > res["years_n"] / 2
    res["verdict"] = "pass" if ok and res["t"] >= res["bar"] else "lead" if ok and res["t"] >= 2 else "fail"
    print(f"{name:28s} {ticker:6s} n={res['n']:4d} mean={res['mean']:+.4f}% (gross {res['gross']:+.4f}) "
          f"t={res['t']:+.2f} bar={res['bar']:.2f} | halves (split {res['split']}) {res['h1']:+.4f}% "
          f"t={res['h1_t']:+.2f} / {res['h2']:+.4f}% t={res['h2_t']:+.2f} | years+ "
          f"{res['years_pos']}/{res['years_n']} -> {res['verdict']}")
    return res


def previous_session(index: pd.DatetimeIndex, day: pd.Timestamp, k: int = 1) -> pd.Timestamp | None:
    """The k-th trading session strictly before `day`."""
    i = index.searchsorted(day) - k
    return index[i] if i >= 0 else None


def nyse_holidays(index: pd.DatetimeIndex, unscheduled: set) -> list:
    """Weekdays inside the span with no session, minus closures nobody could know in advance."""
    days = pd.bdate_range(index[0], index[-1])
    return [d for d in days.difference(index) if d not in unscheduled]

