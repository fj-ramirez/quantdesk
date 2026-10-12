"""Observe-and-test loop for eurusd-burst-fade (docs/edges/eurusd-burst-fade.md, *Observe-and-test
loop*). Each iteration is frozen in the doc before it runs; this file implements them as written.

    uv run --directory backend python ../scripts/edges/2026-10-12-pullback-loop.py data/broker-export <iteration> [--holdout]
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIP, PT = 1e-4, 1e-5
NY = "America/New_York"
D = Path(sys.argv[1])
IT = int(sys.argv[2])
HOLDOUT = "--holdout" in sys.argv

m1 = pd.read_parquet(D / "eurusd.parquet").sort_values("ts").set_index("ts")
m1.index = m1.index.tz_convert(None)
m1 = m1[~m1.index.duplicated()]
T = m1.index.to_numpy().astype("datetime64[ns]")
O, H, L, C, SP = (m1[k].to_numpy(float) for k in ("open", "high", "low", "close", "spread"))
SPX = SP * PT
CLOSE_T = T + np.timedelta64(1, "m")  # an M1 bar is known at its close


def bars(rule: str) -> pd.DataFrame:
    if rule == "1D":
        ny = m1.index.tz_localize("UTC").tz_convert(NY)
        key = (ny - pd.Timedelta(hours=17)).normalize().tz_localize(None)
        b = m1.groupby(key).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                                last_ts=("close", lambda s: s.index[-1]))
        b.index = b["last_ts"] + pd.Timedelta(minutes=1)  # known when its last minute closes
        return b.drop(columns="last_ts")
    b = m1.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    b.index = b.index + pd.Timedelta(rule)
    return b


def asof(series: pd.Series) -> np.ndarray:
    """Value of a completed-bar series as known at each M1 close."""
    idx = series.index.to_numpy().astype("datetime64[ns]")
    pos = np.searchsorted(idx, CLOSE_T, side="right") - 1
    v = series.to_numpy(float)[np.clip(pos, 0, None)]
    v[pos < 0] = np.nan
    return v


def stoch_k(b, n=14, k=3):
    ll, hh = b["low"].rolling(n).min(), b["high"].rolling(n).max()
    return (100 * (b["close"] - ll) / (hh - ll)).rolling(k).mean()


def rsi(c, n=14):
    d = c.diff()
    return 100 - 100 / (1 + d.clip(lower=0).ewm(alpha=1 / n).mean() / (-d.clip(upper=0)).ewm(alpha=1 / n).mean())


B = {r: bars(r) for r in ("5min", "15min", "1h", "1D")}
d1 = B["1D"]["close"]
e20, e50, e200 = (asof(d1.ewm(span=n).mean()) for n in (20, 50, 200))
_d1 = B["1D"]
_tr = pd.concat([_d1["high"] - _d1["low"], (_d1["high"] - _d1["close"].shift(1)).abs(), (_d1["low"] - _d1["close"].shift(1)).abs()], axis=1).max(axis=1)
D1ATR = asof(_tr.ewm(alpha=1 / 14).mean())
E50 = e50
X = {
    "d1_bear": (e20 < e50) & (e50 < e200) & (C < e50),
    "d1_bull": (e20 > e50) & (e50 > e200) & (C > e50),
}
for tf, name in (("15min", "m15"), ("1h", "h1"), ("5min", "m5")):
    c = B[tf]["close"]
    mid, sd = asof(c.rolling(20).mean()), asof(c.rolling(20).std())
    X[f"{name}_pctb"] = (C - (mid - 2 * sd)) / (4 * sd)
    X[f"{name}_stoch"] = asof(stoch_k(B[tf]))
    X[f"{name}_rsi"] = asof(rsi(c))


# ------------------------------------------------------------------ iterations (frozen in the doc)
def iteration(n: int):
    """Returns (sell_entry, buy_entry, sell_exit, buy_exit) boolean arrays over M1 closes."""
    if n == 1:
        se = X["d1_bear"] & (X["m15_pctb"] >= 0.85) & (X["m5_stoch"] >= 75)
        be = X["d1_bull"] & (X["m15_pctb"] <= 0.15) & (X["m5_stoch"] <= 25)
        sx = X["m5_stoch"] < 20
        bx = X["m5_stoch"] > 80
        return se, be, sx, bx
    if n == 2:
        se = X["d1_bear"] & (X["h1_pctb"] >= 0.8) & (X["m15_pctb"] >= 0.8) & (X["m5_pctb"] < 0.9)
        be = X["d1_bull"] & (X["h1_pctb"] <= 0.2) & (X["m15_pctb"] <= 0.2) & (X["m5_pctb"] > 0.1)
        none = np.zeros(len(T), bool)
        return se, be, none, none
    if n == 3:
        se = X["d1_bear"] & (X["h1_pctb"] >= 0.5) & (X["m15_pctb"] >= 0.5)
        be = X["d1_bull"] & (X["h1_pctb"] <= 0.5) & (X["m15_pctb"] <= 0.5)
        none = np.zeros(len(T), bool)
        return se, be, none, none
    if n == 4:
        se = X["d1_bear"] & (X["h1_pctb"] >= 0.5) & (X["m15_pctb"] >= 0.5)
        none = np.zeros(len(T), bool)
        return se, none, none, none
    if n in (5, 6):
        base = X["d1_bear"] & (X["h1_pctb"] >= 0.5) & (X["m15_pctb"] >= 0.5)
        se = base & (X["m5_pctb"] >= 1.0) if n == 5 else base & ((E50 - C) <= 0.6 * D1ATR)
        none = np.zeros(len(T), bool)
        return se, none, none, none
    raise SystemExit(f"iteration {n} is not frozen yet")


EXIT = {1: "signal", 2: "trail", 3: "trail", 4: "time8h", 5: "time4h", 6: "time4h"}.get(IT, "trail")


SE, BE, SX, BX = (np.nan_to_num(a, nan=0).astype(bool) for a in iteration(IT))
STOP, HOLD = (40 if EXIT == "time4h" else 25) * PIP, np.timedelta64(24, "h")


def windows(wins):
    m = np.zeros(len(T), bool)
    for a, b in wins:
        m |= (T >= np.datetime64(a, "ns")) & (T < np.datetime64(b, "ns"))
    return m


def trail_exit(k, side, e, j_end, on=5 * PIP, trail=3 * PIP):
    """#429's exit: stop 25, trail 3 once +5, time stop at j_end. Returns (gross pips, exit index)."""
    seg = slice(k, j_end)
    if side == -1:
        best, worst, ao = e - (L[seg] + SPX[seg]), e - (H[seg] + SPX[seg]), e - (O[seg] + SPX[seg])
    else:
        best, worst, ao = H[seg] - e, L[seg] - e, O[seg] - e
    bb = np.maximum(np.concatenate([[0], np.maximum.accumulate(best)[:-1]]), 0)
    sh = worst <= -STOP
    lvl = bb - trail
    th = (bb >= on) & (worst <= lvl)
    fs = int(np.argmax(sh)) if sh.any() else 10**9
    ft = int(np.argmax(th)) if th.any() else 10**9
    if fs == ft == 10**9:
        px = O[j_end] + SPX[j_end] if side == -1 else O[j_end]
        return ((e - px) if side == -1 else (px - e)) / PIP, j_end
    if fs <= ft:
        return min(-STOP, ao[fs]) / PIP, k + fs
    return min(lvl[ft], ao[ft]) / PIP, k + ft


def simulate(wins) -> pd.DataFrame:
    ok = windows(wins)
    sig = np.where(SE & ok, -1, np.where(BE & ok, 1, 0))
    idx = np.flatnonzero(sig)
    rows, i_free = [], 0
    for i in idx:
        if i < i_free:
            continue
        k = i + 1  # fill at the next M1 open
        if k >= len(T) or (T[k] - CLOSE_T[i]) > np.timedelta64(10, "m") or not (0 < SP[k] <= 15):
            continue
        side = int(sig[i])
        j_end = np.searchsorted(T, T[k] + HOLD)
        if j_end >= len(T):
            break
        seg = slice(k, j_end)
        if side == -1:
            e = O[k]
            stop_hit = H[seg] + SPX[seg] >= e + STOP
            xs = SX[seg]
        else:
            e = O[k] + SPX[k]
            stop_hit = L[seg] <= e - STOP
            xs = BX[seg]
        fs = int(np.argmax(stop_hit)) if stop_hit.any() else 10**9
        fx = int(np.argmax(xs)) if xs.any() else 10**9  # signal at that bar's close -> next open
        if EXIT in ("time8h", "time4h"):
            j_t = np.searchsorted(T, T[k] + np.timedelta64(8 if EXIT == "time8h" else 4, "h"))
            seg8 = slice(k, j_t)
            sh = (H[seg8] + SPX[seg8] >= e + STOP) if side == -1 else (L[seg8] <= e - STOP)
            if sh.any():
                j = k + int(np.argmax(sh))
                opn = O[j] + SPX[j] if side == -1 else O[j]
                px = max(e + STOP, opn) if side == -1 else min(e - STOP, opn)
                kind = "stop"
            else:
                j = j_t
                px = O[j] + SPX[j] if side == -1 else O[j]
                kind = "time"
            g = (e - px) / PIP if side == -1 else (px - e) / PIP
            rows.append((pd.Timestamp(T[k]), pd.Timestamp(T[j]), side, g, SPX[k] / PIP, kind))
            i_free = j
            continue
        if EXIT == "trail":
            g, j = trail_exit(k, side, e, j_end)
            rows.append((pd.Timestamp(T[k]), pd.Timestamp(T[j]), side, g, SPX[k] / PIP, "trail/stop/time"))
            i_free = j
            continue
        if fs <= fx and fs < 10**9:
            j = k + fs
            opn = O[j] + SPX[j] if side == -1 else O[j]
            px = max(e + STOP, opn) if side == -1 else min(e - STOP, opn)
            kind = "stop"
        elif fx < 10**9 and k + fx + 1 < len(T):
            j = k + fx + 1
            px = O[j] + SPX[j] if side == -1 else O[j]
            kind = "signal"
        else:
            j = j_end
            px = O[j] + SPX[j] if side == -1 else O[j]
            kind = "time"
        gross = (e - px) / PIP if side == -1 else (px - e) / PIP
        rows.append((pd.Timestamp(T[k]), pd.Timestamp(T[j]), side, gross, SPX[k] / PIP, kind))
        i_free = j
    tr = pd.DataFrame(rows, columns=["t0", "t1", "side", "gross", "spread", "kind"])
    tr["net"] = tr["gross"] - tr["spread"]
    return tr


def clustered_t(tr):
    x = tr["net"]
    day = tr["t0"].dt.tz_localize("UTC").dt.tz_convert(NY).dt.date.to_numpy()
    dev = (x - x.mean()).groupby(day).sum()
    return x.mean() / (math.sqrt((dev ** 2).sum()) / len(x))


def report(name, tr, k=None):
    if tr.empty:
        print(f"\n== {name}: no trades")
        return tr
    yrs = tr.groupby(tr["t0"].dt.year)["net"].agg(["size", "mean", "sum"])
    t = clustered_t(tr) if len(tr) > 2 else float("nan")
    w = tr["net"] > 0
    months = max(tr["t0"].dt.to_period("M").nunique(), 1)
    print(f"\n== {name}: n={len(tr)} mean {tr['net'].mean():+.2f} pips (gross {tr['gross'].mean():+.2f}) t {t:+.2f} "
          f"win {w.mean():.0%} avg win {tr.loc[w, 'net'].mean():+.1f} avg loss {tr.loc[~w, 'net'].mean():+.1f} "
          f"trades/month {len(tr) / months:.1f}")
    print(f"   exits {tr['kind'].value_counts().to_dict()} | sells {tr.loc[tr.side == -1, 'net'].mean():+.2f} "
          f"(n={(tr.side == -1).sum()}) buys {tr.loc[tr.side == 1, 'net'].mean():+.2f} (n={(tr.side == 1).sum()})")
    print("   years:", " ".join(f"{y}:{r['sum']:+.0f}({int(r['size'])})" for y, r in yrs.iterrows()))
    if k is not None:
        bar = max(2.0, math.sqrt(2 * math.log(k))) if k > 1 else 2.0
        ok = len(tr) >= 100 and tr["net"].mean() > 0 and t >= bar and (yrs["sum"] > 0).sum() >= 3
        print(f"   gate: t >= {bar:.2f}, >=3/4 years, n>=100 -> {'PASS' if ok else 'fail'}")
    return tr


# replication check on the burst weeks (reference only)
obs = pd.read_csv(D / "burst-entries.csv", parse_dates=["ot"])
sells = (obs.loc[obs.side == "sell", "ot"] - pd.Timedelta(hours=3)).to_numpy().astype("datetime64[ns]")
isw = windows([("2026-04-27", "2026-07-11")])
s_ev = CLOSE_T[SE & isw]
recall = np.mean([((s_ev >= b - np.timedelta64(30, "m")) & (s_ev <= b + np.timedelta64(5, "m"))).any() for b in sells])
epi = s_ev[np.concatenate([[True], np.diff(s_ev) > np.timedelta64(30, "m")])] if len(s_ev) else s_ev
prec = np.mean([((sells >= x - np.timedelta64(5, "m")) & (sells <= x + np.timedelta64(30, "m"))).any() for x in epi]) if len(epi) else float("nan")
print(f"iteration {IT}: burst-week replication: recall {recall:.2f}, precision {prec:.2f}, sell episodes/day {len(epi) / 52:.1f} (provider ~1.3)")
report("burst weeks (reference, in-sample)", simulate([("2026-04-27", "2026-07-11")]))
if HOLDOUT:
    report("HOLDOUT 2023-01 -> 2026-04-26 + 2026-07-11 -> 10-05", simulate([("2023-01-01", "2026-04-27"), ("2026-07-11", "2026-10-06")]), k=2)
else:
    report("DEVELOPMENT 2019-2022", simulate([("2019-01-01", "2023-01-01")]), k=IT)


# ------------------------------------------------------------------ observation step (burst weeks only)
if "--diagnose" in sys.argv:
    def ride(k, side):
        j_end = np.searchsorted(T, T[k] + HOLD)
        if EXIT == "trail":
            return trail_exit(k, side, O[k] if side == -1 else O[k] + SPX[k], j_end)
        seg = slice(k, j_end)
        e = O[k] if side == -1 else O[k] + SPX[k]
        stop_hit = (H[seg] + SPX[seg] >= e + STOP) if side == -1 else (L[seg] <= e - STOP)
        xs = SX[seg] if side == -1 else BX[seg]
        fs = int(np.argmax(stop_hit)) if stop_hit.any() else 10**9
        fx = int(np.argmax(xs)) if xs.any() else 10**9
        if fs <= fx and fs < 10**9:
            return -STOP / PIP, fs
        j = k + fx + 1 if fx < 10**9 else j_end
        px = O[j] + SPX[j] if side == -1 else O[j]
        return ((e - px) if side == -1 else (px - e)) / PIP, j - k

    pk = np.searchsorted(T, sells.astype("datetime64[m]").astype("datetime64[ns]"))
    res = np.array([ride(k, -1)[0] for k in pk])
    print(f"\n-- provider's sell entries with iteration {IT}'s exit: mean {res.mean():+.2f}, win {np.mean(res > 0):.0%}")
    tr = simulate([("2026-04-27", "2026-07-11")])
    rk = np.searchsorted(T, tr.loc[tr.side == -1, "t0"].to_numpy().astype("datetime64[ns]")) - 1  # signal bar
    hour_ny = pd.DatetimeIndex(T).tz_localize("UTC").tz_convert(NY).hour.to_numpy()
    ret = lambda n: (C - np.roll(C, n)) / PIP
    feats = {"m15_pctb": X["m15_pctb"], "h1_pctb": X["h1_pctb"], "m5_stoch": X["m5_stoch"], "m15_rsi": X["m15_rsi"],
             "h1_rsi": X["h1_rsi"], "m5_pctb": X["m5_pctb"], "ret15": ret(15), "ret60": ret(60), "ret240": ret(240),
             "ret1440": ret(1440), "hour_ny": hour_ny, "spread": SP}
    pv = pk - 1  # last closed bar before the provider's entry
    print("   feature      provider-sells  rule-sells   (medians, burst weeks)")
    for f, a in feats.items():
        print(f"   {f:12s} {np.nanmedian(a[pv]):12.2f} {np.nanmedian(a[rk]):11.2f}")
    print("   rule sells by result:", tr.loc[tr.side == -1, "net"].describe()[["count", "mean"]].round(2).to_dict())


if "--timing" in sys.argv:
    # how long had this iteration's sell condition been continuously true at each provider sell?
    pk = np.searchsorted(T, sells.astype("datetime64[m]").astype("datetime64[ns]")) - 1
    run_start = np.zeros(len(SE), int)
    cur = -1
    for i, v in enumerate(SE):
        if v and cur < 0:
            cur = i
        elif not v:
            cur = -1
        run_start[i] = cur
    on = SE[pk]
    age = np.where(on, pk - run_start[pk], -1)
    # minutes since the condition was last true (if off now), within 4 h
    last_true = np.full(len(pk), -1)
    for n, k in enumerate(pk):
        w = np.flatnonzero(SE[max(0, k - 240):k + 1])
        last_true[n] = (k - (max(0, k - 240) + w[-1])) if len(w) else -1
    print(f"\n-- iteration {IT} condition at the provider's sells: true now {on.mean():.0%}; "
          f"true within the last 4 h {np.mean(last_true >= 0):.0%}")
    print("   minutes the condition had been on (when on):", np.sort(age[on]).tolist())
    print("   minutes since it was last on (when off, within 4 h):", np.sort(last_true[(~on) & (last_true >= 0)]).tolist())
    # rule episodes in the burst weeks: P&L by entry delay after the episode starts (one-shot each)
    isw_idx = np.flatnonzero(SE & isw)
    starts = isw_idx[np.concatenate([[True], np.diff(isw_idx) > 1])]
    for d in (0, 5, 15, 30, 60):
        res = []
        for st in starts:
            k = st + d
            if k < len(SE) and SE[k] and k + 1 < len(T):
                j_end = np.searchsorted(T, T[k + 1] + HOLD)
                res.append(trail_exit(k + 1, -1, O[k + 1], j_end)[0])
        res = np.array(res)
        print(f"   enter {d:2d} min into a run (if still on): n={len(res)} mean {res.mean():+.2f} win {np.mean(res > 0):.0%}")


if "--forward" in sys.argv:
    # raw forward return (mid ~ bid, no costs) after each new sell/buy signal run, signed to the side
    def fwd(mask, side, wins, hz):
        m = mask & windows(wins)
        idx = np.flatnonzero(m)
        starts = idx[np.concatenate([[True], np.diff(idx) > 30])] if len(idx) else idx
        out = {}
        for h in hz:
            j = np.searchsorted(T, T[starts] + np.timedelta64(h, "m"))
            ok = j < len(T)
            r = (C[j[ok]] - C[starts[ok]]) / PIP * side
            out[h] = (len(r), r.mean(), r.std() / math.sqrt(max(len(r), 1)))
        return out
    hz = (15, 60, 240, 480)
    for nm, wins in (("burst weeks", [("2026-04-27", "2026-07-11")]), ("dev 2019-22", [("2019-01-01", "2023-01-01")]),
                     ("all 2019-26 ex bursts", [("2019-01-01", "2026-04-27"), ("2026-07-11", "2026-10-06")])):
        for side_nm, mask, side in (("sell", SE, -1), ("buy", BE, 1)):
            o = fwd(mask, side, wins, hz)
            print(f"   it{IT} {nm:22s} {side_nm}: " + "  ".join(f"{h}m {o[h][1]:+.2f}±{o[h][2]:.2f} (n={o[h][0]})" for h in hz))
    pk = np.searchsorted(T, sells.astype("datetime64[m]").astype("datetime64[ns]"))
    print("   provider sells (burst weeks): " + "  ".join(
        f"{h}m {np.mean([(C[np.searchsorted(T, T[k] + np.timedelta64(h, 'm'))] - O[k]) / PIP * -1 for k in pk]):+.2f}" for h in hz))
