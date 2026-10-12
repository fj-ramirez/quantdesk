"""Backlog 36-39: when does the 5-minute ORB (#400-402) work? Same trade, four filters known by
09:35. Ledger tests 410-417 (filter-major, instrument-minor). The unfiltered ORB is printed as
context (it is #400-401, not re-counted)."""
import sys

import numpy as np
import pandas as pd

from _common import load, rth_daily, sessions, summarize

data = sys.argv[1]
M = pd.Timedelta(minutes=1)


def orb_days(sym):
    df = load(data, sym)
    ts, op, hi, lo, cl, sp, tv = (df.index, df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(),
                                  df["close"].to_numpy(), df["spread"].to_numpy(), df["tick_volume"].to_numpy())
    rth = rth_daily(data, sym)
    rng = rth["high"] - rth["low"]
    nr7 = (rng == rng.rolling(7).min())
    out = []
    for day in sessions(data, sym):
        a, e, z = (ts.searchsorted(day + m * M) for m in (570, 575, 955))
        if e - a < 3 or z - e < 10 or (ts[a] - (day + 570 * M)).total_seconds() > 120:
            continue
        o, c = op[a], cl[e - 1]
        if c == o:
            continue
        side = 1 if c > o else -1
        H, L = hi[a:e].max(), lo[a:e].min()
        entry, stop = op[e], (L if side > 0 else H)
        risk = (entry - stop) * side
        if risk <= 0:
            continue
        target = entry + side * 10 * risk
        s_hit = (lo[e:z] <= stop) if side > 0 else (hi[e:z] >= stop)
        t_hit = ((hi[e:z] >= target) if side > 0 else (lo[e:z] <= target)).copy()
        t_hit[0] = False
        si = int(s_hit.argmax()) if s_hit.any() else None
        ti = int(t_hit.argmax()) if t_hit.any() else None
        if si is not None and (ti is None or si <= ti):
            k = e + si
            px = op[k] if si > 0 and (op[k] - stop) * side < 0 else stop
        elif ti is not None:
            k = e + ti
            px = op[k] if (op[k] - target) * side > 0 else target
        else:
            px = cl[z - 1]
        move = side * (px - entry)
        prev = rth.index[rth.index < day]
        p16 = ts.searchsorted(prev[-1] + 960 * M) if len(prev) else None
        p16b = ts.searchsorted(prev[-2] + 960 * M) if len(prev) > 1 else None
        out.append({"day": day, "t0": ts[e], "side": side, "vol": tv[a:e].sum(),
                    "nr7_prev": bool(nr7.get(prev[-1], False)) if len(prev) else False,
                    "prev_dir": np.sign(op[p16] - op[p16b]) if p16b is not None else 0,
                    "gap": (o / op[p16] - 1) if p16 is not None else np.nan,
                    "pct": (move - 2 * sp[e]) / entry * 100, "gross_pct": move / entry * 100,
                    "cost_pct": 2 * sp[e] / entry * 100, "swap_pct": 0.0, "usd_per_001": 0.0})
    t = pd.DataFrame(out)
    t["rvol"] = t["vol"] / t["vol"].rolling(14).mean().shift(1)
    return t


n = 409
syms = ["NAS100.fs", "S&P.fs"]
tabs = {s: orb_days(s) for s in syms}
for s in syms:
    summarize("   context: unfiltered ORB", s, tabs[s], 400)
filters = [("36 ORB | rel. volume >= 2", lambda t: t["rvol"] >= 2),
           ("37 ORB | prior day NR7", lambda t: t["nr7_prev"]),
           ("38 ORB | aligned with prior day", lambda t: t["side"] == t["prev_dir"]),
           ("39 ORB | gap >= 0.3% same way", lambda t: (t["gap"] * t["side"]) >= 0.003)]
for name, f in filters:
    for s in syms:
        n += 1
        t = tabs[s]
        summarize(name, s, t[f(t).fillna(False).astype(bool)], n)
