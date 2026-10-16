"""T149: stage-1 test of docs/edges/xau-range-rejection.md, the frozen rule, run once. Ledger 532.

Levels from GLD daily bars (Yahoo, the source of gex.daily_bars) up to the previous session:
R_hi / R_lo over 8 sessions, ATR(14), SMA50. Regime on when close < SMA50 and R_hi - R_lo <= 3 ATR.
Each is scaled to XAUUSD by the previous session's ratio XAUUSD(16:00 NY) / GLD close, the `1d`
basis rebuilt from the same M1 bars (broker.basis itself only starts at T145).
On XAUUSD M1 (bid) aggregated to 15-minute bars, 07:00-19:00 UTC: armed once a bar's high reaches
the zone [R_hi - 0.3 ATR, R_hi]; a bar closing below the zone's lower edge triggers a short at that
close (arming and trigger may be the same bar); a high at or above the stop before the trigger
cancels the session. One attempt per session. Exit on M1: stop R_hi + 0.25 ATR (checked first),
target R_lo + 0.25 ATR, else the first bar at or after 20:30 UTC. Cost: the M1 spread at entry,
doubled. No calendar blackout: there is no timestamped release history before T139.
The same trigger on regime-off days is printed as context ("flat when off").
"""
import math
import sys

import numpy as np
import pandas as pd
import yfinance as yf
from _common import load

data = sys.argv[1]
raw = yf.download("GLD", start="2018-09-01", end="2026-10-10", auto_adjust=False, progress=False)
g = pd.DataFrame({f.lower(): raw[f].squeeze() for f in ("High", "Low", "Close")}).dropna()
g.index = pd.DatetimeIndex(g.index).normalize()
prev_c = g["close"].shift()
tr = pd.concat([g["high"] - g["low"], (g["high"] - prev_c).abs(), (g["low"] - prev_c).abs()], axis=1).max(axis=1)
lv = pd.DataFrame({"r_hi": g["high"].rolling(8).max(), "r_lo": g["low"].rolling(8).min(),
                   "atr": tr.rolling(14).mean(), "sma50": g["close"].rolling(50).mean(), "close": g["close"]})

xau = load(data, "XAUUSD")  # NY-indexed M1
xau_utc = xau.set_index(xau["ts"]).sort_index()
ny_close = (g.index.tz_localize("America/New_York") + pd.Timedelta(hours=16))
i = xau.index.searchsorted(ny_close)
ok = i < len(xau)
i = np.where(ok, i, len(xau) - 1)
ok &= (xau.index[i] - ny_close).total_seconds() < 600
lv["ratio"] = np.where(ok, xau["open"].to_numpy()[i] / g["close"].to_numpy(), np.nan)

rows = []
sessions = g.index[(g.index >= pd.Timestamp("2019-08-01"))]
for k in range(1, len(sessions)):
    d, p = sessions[k], lv.loc[:sessions[k]].iloc[-2]  # levels from the previous session
    if p[["r_hi", "r_lo", "atr", "sma50", "ratio"]].isna().any():
        continue
    on = bool(p.close < p.sma50 and p.r_hi - p.r_lo <= 3 * p.atr)
    s = p.ratio
    hi, lo, atr = p.r_hi * s, p.r_lo * s, p.atr * s
    z_lo, stop, target = hi - 0.3 * atr, hi + 0.25 * atr, lo + 0.25 * atr
    day = xau_utc.loc[pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=7):
                      pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=18, minutes=59)]
    if day.empty:
        continue
    q = day.resample("15min").agg({"high": "max", "low": "min", "close": "last", "spread": "last"}).dropna()
    armed, entry = False, None
    for t, b in q.iterrows():
        if b.high >= stop:
            break
        armed |= b.high >= z_lo
        if armed and b.close < z_lo:
            entry = (t + pd.Timedelta(minutes=15), b.close, b.spread)
            break
    if entry is None:
        continue
    t0, px, spr = entry
    path = xau_utc.loc[t0: pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=20, minutes=30)]
    r = stop - px
    if r <= 0 or path.empty:
        continue
    exit_px, how = None, "time"
    for _, m in path.iloc[:-1].iterrows():
        if m.high >= stop:
            exit_px, how = stop, "stop"
            break
        if m.low <= target:
            exit_px, how = target, "target"
            break
    if exit_px is None:
        exit_px = path["open"].iloc[-1]
    rows.append({"date": d, "on": on, "r": (px - exit_px) / r - 2 * spr / r, "gross": (px - exit_px) / r,
                 "how": how, "risk_usd": r, "cost_r": 2 * spr / r})

df = pd.DataFrame(rows)


def report(name, x):
    v = x["r"].to_numpy()
    if len(v) < 3:
        print(f"{name}: n={len(v)}")
        return
    se = v.std(ddof=1) / math.sqrt(len(v))
    yrs = x.groupby(x["date"].dt.year)["r"].agg(["sum", "count"])
    oos = x["date"] >= "2024-01-01"
    print(f"{name}: n={len(v)} mean R={v.mean():+.3f} ± {se:.3f} (t {v.mean() / se:+.2f}), gross {x.gross.mean():+.3f}, "
          f"cost {x.cost_r.mean():.3f}R, median risk ${x.risk_usd.median():.1f} | IS {x[~oos].r.mean():+.3f} "
          f"(n {(~oos).sum()}) OOS {x[oos].r.mean():+.3f} (n {oos.sum()}) | exits {x.how.value_counts().to_dict()}")
    print("   years:", " ".join(f"{y}:{s:+.1f}R/{c}" for y, (s, c) in yrs.iterrows()))


report("T149 regime ON (the rule)", df[df["on"]])
report("context: regime OFF (same trigger)", df[~df["on"]])
