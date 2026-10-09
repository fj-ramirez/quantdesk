"""Four literature-backed effects, declared before running. No parameter search.

H1 overnight drift: long index CFD at the 16:00 NY close, out at 09:30 NY next session.
   (Equity returns accrue mostly overnight: Cliff/Cooper/Gulen 2008, Lou/Polk/Skouras 2019.)
H2 intraday leg, for contrast: long 09:30 -> 16:00.
H3 turn of the month: long from the close of the last trading day of the month to the close of
   the 3rd trading day of the next (Ariel 1987, Lakonishok/Smidt 1988).
H4 trend filter: long the next session's close-to-close while the prior close > 200-day SMA, else flat
   (Faber 2007; Moskowitz/Ooi/Pedersen 2012 time-series momentum).
H5 gold by session: long XAUUSD 18:00->03:00 NY (Asia) and short 08:20->16:00 NY (NY)
   (Asian-session gains / NY-session losses in gold, documented e.g. by Bespoke, Nasdaq/CME notes 2010s).

Cost: recorded spread at entry x2 (one spread per round trip). Futures-CFD roll jumps are removed:
any hold spanning a broker.rolls timestamp has the roll step (step * price) subtracted.
Report per-trade mean in points and in % of price, t by trade, IS (<2024) vs OOS (>=2024), years.
"""
import sys

import numpy as np
import pandas as pd

D = sys.argv[1]
NY = "America/New_York"
rolls = pd.read_csv(f"{D}/rolls.csv", parse_dates=["rolled_at"])


def load(sym):
    df = pd.read_parquet(f"{D}/{sym.replace('&', '-').lower()}.parquet")
    df["ny"] = df["ts"].dt.tz_convert(NY)
    df["spread"] = df["spread"] * 0.01
    return df.set_index("ny").sort_index()


def at(df, times):
    """Open of the first M1 bar at or after each instant (the price you could act at), and its spread."""
    idx = df.index.searchsorted(times)
    ok = idx < len(df)
    idx = np.where(ok, idx, len(df) - 1)
    lag = (df.index[idx] - times).total_seconds()
    ok &= lag < 600  # a bar within 10 minutes, else no trade
    return df["open"].to_numpy()[idx], df["spread"].to_numpy()[idx], ok


def roll_adj(sym, t0, t1, px):
    r = rolls[rolls.cfd_symbol == sym]
    adj = np.zeros(len(t0))
    for _, row in r.iterrows():
        ts = row.rolled_at.tz_convert(NY)
        hit = (t0 <= ts) & (t1 > ts)
        adj[hit] += row.step * px[hit]
    return adj


def report(name, sym, t0, t1, side=1):
    df = load(sym)
    p0, s0, ok0 = at(df, t0)
    p1, _, ok1 = at(df, t1)
    ok = ok0 & ok1
    pnl = side * (p1 - p0 - roll_adj(sym, t0, t1, p0)) - 2 * s0
    gross = pnl + 2 * s0
    pnl, gross, p0, t0 = pnl[ok], gross[ok], p0[ok], t0[ok]
    pct = pnl / p0 * 100
    yrs = pd.Series(pct, index=t0.year).groupby(level=0).sum()
    oos = t0 >= pd.Timestamp("2024-01-01", tz=NY)

    def stats(x):
        return f"n={len(x):4d} mean={x.mean():+.4f}% t={x.mean() / x.std(ddof=1) * np.sqrt(len(x)):+.2f}"

    print(f"{name:28s} {sym:10s} gross/trade={np.mean(gross / p0 * 100):+.4f}%  cost={np.mean(2 * s0[ok] / p0 * 100):.4f}%")
    print(f"   IS  {stats(pct[~oos])}   OOS {stats(pct[oos])}   total {pct.sum():+.1f}% simple")
    print("   years:", " ".join(f"{y}:{v:+.1f}" for y, v in yrs.items()))


def sessions(sym):
    df = load(sym)
    days = pd.DatetimeIndex(sorted(set(df.index.normalize()))).tz_convert(NY)
    # a US session is a weekday with a bar at 15:59 NY
    closes = df.index[(df.index.hour == 15) & (df.index.minute == 59)].normalize()
    return pd.DatetimeIndex(sorted(set(closes)))


for sym in ["S&P.fs", "NAS100.fs", "DJ30.fs"]:
    d = sessions(sym)
    close_t = d + pd.Timedelta(hours=16)
    open_next = d[1:] + pd.Timedelta(hours=9, minutes=30)
    report("H1 overnight 16:00->09:30", sym, close_t[:-1], open_next)
    report("H2 intraday 09:30->16:00", sym, d + pd.Timedelta(hours=9, minutes=30), close_t)
    # H3 turn of month
    s = pd.Series(d, index=d)
    month = s.dt.to_period("M")
    last = s.groupby(month).max()
    third = s.groupby(month).apply(lambda x: x.iloc[2] if len(x) >= 3 else pd.NaT)
    pairs = [(l, third.iloc[i + 1]) for i, l in enumerate(last.iloc[:-1]) if pd.notna(third.iloc[i + 1])]
    report("H3 turn of month", sym, pd.DatetimeIndex([a for a, _ in pairs]) + pd.Timedelta(hours=16),
           pd.DatetimeIndex([b for _, b in pairs]) + pd.Timedelta(hours=16))
    # H4 trend filter on daily closes at 16:00
    df = load(sym)
    c, _, okc = at(df, close_t)
    cs = pd.Series(c, index=d).where(okc)
    sig = cs > cs.rolling(200).mean()
    on = sig.to_numpy()[:-1]
    report("H4 >200d SMA, hold 1 day", sym, close_t[:-1][on], close_t[1:][on])

d = sessions("XAUUSD")
report("H5a gold Asia 18:00->03:00", "XAUUSD", d[:-1] + pd.Timedelta(hours=18), d[1:] + pd.Timedelta(hours=3))
report("H5b gold NY short 08:20->16:00", "XAUUSD", d + pd.Timedelta(hours=8, minutes=20), d + pd.Timedelta(hours=16), side=-1)
report("H1 gold overnight 16:00->09:30", "XAUUSD", d[:-1] + pd.Timedelta(hours=16), d[1:] + pd.Timedelta(hours=9, minutes=30))
c, _, okc = at(load("XAUUSD"), d + pd.Timedelta(hours=16))
cs = pd.Series(c, index=d).where(okc)
on = (cs > cs.rolling(200).mean()).to_numpy()[:-1]
report("H4 gold >200d SMA, 1 day", "XAUUSD", (d + pd.Timedelta(hours=16))[:-1][on], (d + pd.Timedelta(hours=16))[1:][on])
