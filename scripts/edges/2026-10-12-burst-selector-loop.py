"""Bounded reverse-engineering loop for the eurusd-burst-fade provider (exploration + OOS tests).

Protocol (docs/edges/eurusd-burst-fade.md, *Selector loop*):
  1. Each family emits sell events (and the mirrored buy events) from bar data known at the time.
  2. In-sample screen on the burst weeks: recall = share of the provider's sells with an event in
     [burst - 30 min, burst + 5 min]; precision = share of event episodes near a burst;
     one-at-a-time P&L with the frozen exit. Screen to OOS: recall >= 0.25 and in-sample
     mean >= +2.5 pips over >= 30 trades.
  3. Families passing the screen are frozen as listed here and run once on 2019-01 -> 2026-04-26
     (+ 2026-07-11 -> 10-05), both sides, cost doubled. Gate: t (clustered by day) >= max(2,
     sqrt(2 ln k)) with k = families sent to OOS, mean > 0, positive in most years.

    uv run --directory backend python ../scripts/edges/2026-10-12-burst-selector-loop.py \
        data/broker-export ../docs/edges/eurusd-burst-fade-observations.csv
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIP, PT = 1e-4, 1e-5
NY = "America/New_York"
D = Path(sys.argv[1])
df = pd.read_parquet(D / "eurusd.parquet").sort_values("ts").set_index("ts")
df.index = df.index.tz_convert(None)
df = df[~df.index.duplicated()]
T = df.index.to_numpy()
O, H, L, C, SP = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "spread"))
SPX = SP * PT

obs = pd.read_csv(sys.argv[2])
obs["t"] = pd.to_datetime(obs["open_ts_utc"], format="mixed")
SELLS = np.sort(obs.loc[obs["direction"] == "sell", "t"].to_numpy().astype("datetime64[ns]"))
IS_WIN = [("2026-04-27", "2026-07-11"), ("2026-10-05", "2026-10-09")]
OOS_WIN = [("2019-01-01", "2026-04-27"), ("2026-07-11", "2026-10-06")]


def in_windows(t: np.ndarray, wins) -> np.ndarray:
    m = np.zeros(len(t), bool)
    for a, b in wins:
        m |= (t >= np.datetime64(a, "ns")) & (t < np.datetime64(b, "ns"))
    return m


# ---------------------------------------------------------------- frozen exit (stage-1 spec)
def exit_pips(k: int, side: int, stop=25, on=5, trail=3):
    j = np.searchsorted(T, T[k] + np.timedelta64(24, "h"))
    if j >= len(T):
        return None, None
    seg = slice(k, j)
    if side == -1:
        e = O[k]
        best, worst, ao = e - (L[seg] + SPX[seg]), e - (H[seg] + SPX[seg]), e - (O[seg] + SPX[seg])
    else:
        e = O[k] + SPX[k]
        best, worst, ao = H[seg] - e, L[seg] - e, O[seg] - e
    bb = np.maximum(np.concatenate([[0], np.maximum.accumulate(best)[:-1]]), 0)
    sh = worst <= -stop * PIP
    lvl = bb - trail * PIP
    th = (bb >= on * PIP) & (worst <= lvl)
    fs = int(np.argmax(sh)) if sh.any() else 10**9
    ft = int(np.argmax(th)) if th.any() else 10**9
    if fs == ft == 10**9:
        return ((O[j] - e) if side == 1 else e - (O[j] + SPX[j])) / PIP, j
    if fs <= ft:
        return min(-stop * PIP, ao[fs]) / PIP, k + fs
    return min(lvl[ft], ao[ft]) / PIP, k + ft


def run(events: pd.Series, wins, max_spread=15) -> pd.DataFrame:
    """events: index = time the signal is known (ns, naive UTC), value = side. One position at a time."""
    ev = events[in_windows(events.index.to_numpy(), wins)].sort_index()
    rows, busy = [], np.datetime64("1970-01-01", "ns")
    for t, side in ev.items():
        tt = np.datetime64(t, "ns")
        if tt < busy:
            continue
        k = np.searchsorted(T, tt)
        if k >= len(T) or (T[k] - tt) > np.timedelta64(10, "m") or not (0 < SP[k] <= max_spread):
            continue
        p, xi = exit_pips(k, int(side))
        if p is None:
            break
        rows.append((pd.Timestamp(T[k]), int(side), p, SPX[k] / PIP))
        busy = T[xi]
    return pd.DataFrame(rows, columns=["t0", "side", "gross", "spread"])


# ---------------------------------------------------------------- indicator helpers
M1 = df[["open", "high", "low", "close", "tick_volume"]]


def bars(tf: str) -> pd.DataFrame:
    """Completed bars of timeframe tf, indexed by the time they complete."""
    b = M1.resample(tf, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "tick_volume": "sum"}).dropna()
    b.index = b.index + pd.Timedelta(tf)
    return b


def rsi(x: pd.Series, n=14) -> pd.Series:
    d = x.diff()
    return 100 - 100 / (1 + d.clip(lower=0).ewm(alpha=1 / n).mean() / (-d.clip(upper=0)).ewm(alpha=1 / n).mean())


def stoch(b: pd.DataFrame, n=14, k=3, d=3):
    ll, hh = b["low"].rolling(n).min(), b["high"].rolling(n).max()
    kk = (100 * (b["close"] - ll) / (hh - ll)).rolling(k).mean()
    return kk, kk.rolling(d).mean()


def atr(b: pd.DataFrame, n=14) -> pd.Series:
    pc = b["close"].shift(1)
    tr = pd.concat([b["high"] - b["low"], (b["high"] - pc).abs(), (b["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n).mean()


def ev(sell: pd.Series, buy: pd.Series) -> pd.Series:
    s = pd.Series(-1, index=sell.index[sell.fillna(False).to_numpy()])
    b = pd.Series(1, index=buy.index[buy.fillna(False).to_numpy()])
    out = pd.concat([s, b]).sort_index()
    return out[~out.index.duplicated()]


def touch_events(level_up: pd.Series, level_dn: pd.Series) -> pd.Series:
    """Intrabar: the M1 high crosses a level fixed at the last completed higher-TF bar (sell),
    or the M1 low crosses the lower level (buy). Event time = that M1 bar's close (known by then)."""
    up = level_up.reindex(M1.index, method="ffill")
    dn = level_dn.reindex(M1.index, method="ffill")
    hs = (M1["high"] >= up) & (M1["high"].shift(1) < up.shift(1))
    ls = (M1["low"] <= dn) & (M1["low"].shift(1) > dn.shift(1))
    e = ev(hs, ls)
    e.index = e.index + pd.Timedelta(minutes=1)
    return e


# ---------------------------------------------------------------- the families
B = {tf: bars(tf) for tf in ("5min", "15min", "1h", "4h")}


def fam_bb_reentry(tf, n=20, k=2.0):
    b = B[tf]
    m, s = b["close"].rolling(n).mean(), b["close"].rolling(n).std()
    up, dn = m + k * s, m - k * s
    return ev((b["close"].shift(1) > up.shift(1)) & (b["close"] < up), (b["close"].shift(1) < dn.shift(1)) & (b["close"] > dn))


def fam_bb_touch(tf, n=20, k=2.0):
    b = B[tf]
    m, s = b["close"].rolling(n).mean(), b["close"].rolling(n).std()
    return touch_events(m + k * s, m - k * s)


def fam_rsi_cross(tf, lvl=70):
    r = rsi(B[tf]["close"])
    return ev((r.shift(1) > lvl) & (r <= lvl), (r.shift(1) < 100 - lvl) & (r >= 100 - lvl))


def fam_stoch_cross(tf, lvl=80):
    kk, dd = stoch(B[tf])
    return ev((kk.shift(1) > dd.shift(1)) & (kk <= dd) & (kk.shift(1) > lvl),
              (kk.shift(1) < dd.shift(1)) & (kk >= dd) & (kk.shift(1) < 100 - lvl))


def fam_keltner_touch(tf, n=20, k=2.0):
    b = B[tf]
    e = b["close"].ewm(span=n).mean()
    a = atr(b)
    return touch_events(e + k * a, e - k * a)


def fam_envelope_touch(tf, n, pct):
    b = B[tf]
    m = b["close"].rolling(n).mean()
    return touch_events(m * (1 + pct / 100), m * (1 - pct / 100))


def fam_pin_at_high(tf, look=16):
    b = B[tf]
    body = (b["close"] - b["open"]).abs()
    rng = b["high"] - b["low"]
    uw = b["high"] - b[["open", "close"]].max(axis=1)
    lw = b[["open", "close"]].min(axis=1) - b["low"]
    top = b["high"] >= b["high"].rolling(look).max()
    bot = b["low"] <= b["low"].rolling(look).min()
    return ev(top & (uw >= 2 * body) & (uw >= 0.5 * rng), bot & (lw >= 2 * body) & (lw >= 0.5 * rng))


def fam_rsi_divergence(tf, look=16):
    b = B[tf]
    r = rsi(b["close"])
    hh = b["high"] >= b["high"].rolling(look).max()
    ll = b["low"] <= b["low"].rolling(look).min()
    return ev(hh & (r < r.rolling(look).max() - 5) & (b["close"] < b["open"]),
              ll & (r > r.rolling(look).min() + 5) & (b["close"] > b["open"]))


def fam_cci_cross(tf, n=20, lvl=100):
    b = B[tf]
    tp = (b["high"] + b["low"] + b["close"]) / 3
    cci = (tp - tp.rolling(n).mean()) / (0.015 * tp.rolling(n).apply(lambda v: np.abs(v - v.mean()).mean(), raw=True))
    return ev((cci.shift(1) > lvl) & (cci <= lvl), (cci.shift(1) < -lvl) & (cci >= -lvl))


def fam_asia_range_fade():
    d = M1.copy()
    day = d.index.normalize()
    asia = d[(d.index.hour < 7)].groupby(d[(d.index.hour < 7)].index.normalize()).agg(h=("high", "max"), l=("low", "min"))
    ah = asia["h"].reindex(day).to_numpy()
    al = asia["l"].reindex(day).to_numpy()
    sess = (d.index.hour >= 7) & (d.index.hour < 16)
    # price ran >= 5 pips beyond the Asian range today, now closes back inside it
    hi_today = d["high"].groupby(day).cummax().to_numpy()
    lo_today = d["low"].groupby(day).cummin().to_numpy()
    c, pc = d["close"].to_numpy(), d["close"].shift(1).to_numpy()
    sell = sess & (hi_today >= ah + 5 * PIP) & (pc >= ah) & (c < ah)
    buy = sess & (lo_today <= al - 5 * PIP) & (pc <= al) & (c > al)
    e = ev(pd.Series(sell, index=d.index), pd.Series(buy, index=d.index))
    e.index = e.index + pd.Timedelta(minutes=1)
    return e


def fam_stall_after_push(push=10, look=120, stall=5):
    """M1: price rose >= push pips over `look` min, the last `stall` min made no new high."""
    c = M1["close"]
    r = (c - c.shift(look)) / PIP
    nohigh = M1["high"].rolling(stall).max() < M1["high"].shift(stall).rolling(look - stall).max()
    nolow = M1["low"].rolling(stall).min() > M1["low"].shift(stall).rolling(look - stall).min()
    e = ev((r >= push) & nohigh & (r.shift(1) >= push) & ~(nohigh.shift(1).fillna(False).astype(bool)),
           (r <= -push) & nolow & (r.shift(1) <= -push) & ~(nolow.shift(1).fillna(False).astype(bool)))
    e.index = e.index + pd.Timedelta(minutes=1)
    return e


FAMILIES = {}
for tf in ("5min", "15min", "1h", "4h"):
    FAMILIES[f"BB(20,2) re-entry {tf}"] = lambda tf=tf: fam_bb_reentry(tf)
    FAMILIES[f"BB(20,2) touch {tf}"] = lambda tf=tf: fam_bb_touch(tf)
    FAMILIES[f"RSI14 cross 70/30 {tf}"] = lambda tf=tf: fam_rsi_cross(tf)
    FAMILIES[f"Stoch(14,3,3) cross >80 {tf}"] = lambda tf=tf: fam_stoch_cross(tf)
for tf in ("15min", "1h"):
    FAMILIES[f"Keltner(20,2ATR) touch {tf}"] = lambda tf=tf: fam_keltner_touch(tf)
    FAMILIES[f"Pin bar at 16-bar extreme {tf}"] = lambda tf=tf: fam_pin_at_high(tf)
    FAMILIES[f"RSI divergence {tf}"] = lambda tf=tf: fam_rsi_divergence(tf)
    FAMILIES[f"CCI20 cross 100 {tf}"] = lambda tf=tf: fam_cci_cross(tf)
FAMILIES["Envelope SMA50 H1 +-0.3%"] = lambda: fam_envelope_touch("1h", 50, 0.3)
FAMILIES["Envelope SMA20 M15 +-0.15%"] = lambda: fam_envelope_touch("15min", 20, 0.15)
FAMILIES["Asian-range false break"] = fam_asia_range_fade
FAMILIES["Stall after 10-pip 2h push"] = lambda: fam_stall_after_push(10, 120, 5)
FAMILIES["Stall after 15-pip 4h push"] = lambda: fam_stall_after_push(15, 240, 10)


def screen(name: str, e: pd.Series) -> dict:
    t = e.index.to_numpy().astype("datetime64[ns]")
    ins = in_windows(t, IS_WIN)
    sells = t[ins & (e.to_numpy() == -1)]
    near = np.array([((sells >= b - np.timedelta64(30, "m")) & (sells <= b + np.timedelta64(5, "m"))).any() for b in SELLS])
    # episodes: sell events more than 30 min apart
    epi = sells[np.concatenate([[True], np.diff(sells) > np.timedelta64(30, "m")])] if len(sells) else sells
    hit = np.array([((SELLS >= x - np.timedelta64(5, "m")) & (SELLS <= x + np.timedelta64(30, "m"))).any() for x in epi])
    tr = run(e[e == -1], IS_WIN)
    days = 52
    return {"family": name, "recall": near.mean(), "precision": hit.mean() if len(hit) else np.nan,
            "episodes_per_day": len(epi) / days, "is_n": len(tr), "is_mean": tr["gross"].mean() if len(tr) else np.nan,
            "is_win": (tr["gross"] > 0).mean() if len(tr) else np.nan}


def clustered_t(x: pd.Series, g) -> float:
    dev = (x - x.mean()).groupby(np.asarray(g)).sum()
    return x.mean() / (math.sqrt((dev ** 2).sum()) / len(x))


if __name__ == "__main__":
    res = []
    events = {}
    for name, f in FAMILIES.items():
        e = f()
        events[name] = e
        r = screen(name, e)
        res.append(r)
        print(f"{name:34s} recall {r['recall']:.2f} prec {r['precision']:.2f} epi/day {r['episodes_per_day']:5.1f} "
              f"| IS sells n={r['is_n']:4d} mean {r['is_mean']:+.2f} win {r['is_win']:.0%}", flush=True)
    R = pd.DataFrame(res)
    R.to_csv(D / "burst-selector-loop-screen.csv", index=False)
    go = R[(R["recall"] >= 0.25) & (R["is_mean"] >= 2.5) & (R["is_n"] >= 30)]
    print(f"\n{len(go)} of {len(R)} families pass the in-sample screen")
    k = max(len(go), 1)
    bar = max(2.0, math.sqrt(2 * math.log(k))) if k > 1 else 2.0
    for name in go["family"]:
        tr = run(events[name], OOS_WIN)
        tr["net"] = tr["gross"] - tr["spread"]
        day = tr["t0"].dt.tz_localize("UTC").dt.tz_convert(NY).dt.date
        yrs = tr.groupby(tr["t0"].dt.year)["net"].sum()
        t = clustered_t(tr["net"], day)
        ok = tr["net"].mean() > 0 and t >= bar and (yrs > 0).sum() > len(yrs) / 2
        print(f"OOS {name:34s} n={len(tr)} mean {tr['net'].mean():+.3f} t {t:+.2f} (bar {bar:.2f}) "
              f"years+ {(yrs > 0).sum()}/{len(yrs)} sells {tr.loc[tr.side == -1, 'net'].mean():+.2f} "
              f"buys {tr.loc[tr.side == 1, 'net'].mean():+.2f} -> {'PASS' if ok else 'fail'}")
