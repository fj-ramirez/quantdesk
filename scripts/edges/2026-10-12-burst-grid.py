"""Ledger #431: the frozen grid spec in docs/edges/eurusd-burst-fade.md (*Frozen grid spec*). Run once.

    uv run --directory backend python ../scripts/edges/2026-10-12-burst-grid.py data/broker-export
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIP, PT = 1e-4, 1e-5
STEP, MAX_UNITS, TP, STOP = 6 * PIP, 5, 5 * PIP, 25 * PIP
NY = "America/New_York"
df = pd.read_parquet(Path(sys.argv[1]) / "eurusd.parquet").sort_values("ts").reset_index(drop=True)
T = df["ts"].dt.tz_convert(None).to_numpy().astype("datetime64[ns]")
O, H, L, SP = (df[c].to_numpy(float) for c in ("open", "high", "low", "spread"))
SPX = SP * PT

m5 = df.set_index("ts")["close"].resample("5min", label="left", closed="left").last().dropna()
z = ((m5 - m5.rolling(144).mean()) / m5.rolling(144).std()).to_numpy()
sig_t = (m5.index + pd.Timedelta(minutes=5)).tz_convert(None).to_numpy().astype("datetime64[ns]")
fill = np.searchsorted(T, sig_t)


def windows(t, wins):
    m = np.zeros(len(t), bool)
    for a, b in wins:
        m |= (t >= np.datetime64(a, "ns")) & (t < np.datetime64(b, "ns"))
    return m


def basket(k: int, side: int):
    """Returns (pips x units, units used, exit index, kind). side -1 = short."""
    j_end = np.searchsorted(T, T[k] + np.timedelta64(24, "h"))
    if j_end >= len(T):
        return None
    first = O[k] if side == -1 else O[k] + SPX[k]
    fills = [first]
    extra = SPX[k] / PIP  # doubled cost: one more spread per unit
    for i in range(k, j_end):
        # executable prices this bar: short closes at ask, long at bid
        if side == -1:
            adv_px, fav_px, opn = H[i] + SPX[i], L[i] + SPX[i], O[i] + SPX[i]
        else:
            adv_px, fav_px, opn = L[i], H[i], O[i]
        stop_lvl = first + STOP if side == -1 else first - STOP
        if (side == -1 and adv_px >= stop_lvl) or (side == 1 and adv_px <= stop_lvl):
            px = max(stop_lvl, opn) if side == -1 else min(stop_lvl, opn)
            return sum((f - px) if side == -1 else (px - f) for f in fills) / PIP - extra, len(fills), i, "stop"
        while len(fills) < MAX_UNITS:
            nxt = fills[-1] + STEP if side == -1 else fills[-1] - STEP
            hit = (side == -1 and H[i] >= nxt) or (side == 1 and L[i] + SPX[i] <= nxt)
            if not hit:
                break
            if i > k and ((side == -1 and O[i] > nxt) or (side == 1 and O[i] + SPX[i] < nxt)):
                nxt = O[i] if side == -1 else O[i] + SPX[i]
            fills.append(nxt)
            extra += SPX[i] / PIP
        avg = sum(fills) / len(fills)
        tp_lvl = avg - TP if side == -1 else avg + TP
        if (side == -1 and fav_px <= tp_lvl) or (side == 1 and fav_px >= tp_lvl):
            px = min(tp_lvl, opn) if (side == -1 and i > k) else (max(tp_lvl, opn) if (side == 1 and i > k) else tp_lvl)
            return sum((f - px) if side == -1 else (px - f) for f in fills) / PIP - extra, len(fills), i, "tp"
    px = O[j_end] + SPX[j_end] if side == -1 else O[j_end]
    return sum((f - px) if side == -1 else (px - f) for f in fills) / PIP - extra, len(fills), j_end, "time"


def simulate(wins):
    ok = windows(sig_t, wins)
    rows, busy = [], np.datetime64("1970-01-01", "ns")
    for i in range(len(sig_t)):
        if not ok[i] or np.isnan(z[i]) or abs(z[i]) < 1.5 or sig_t[i] < busy:
            continue
        k = fill[i]
        if k >= len(T) or (T[k] - sig_t[i]) > np.timedelta64(10, "m") or not (0 < SP[k] <= 15):
            continue
        side = -1 if z[i] >= 1.5 else 1
        r = basket(k, side)
        if r is None:
            break
        p, units, xi, kind = r
        rows.append((pd.Timestamp(T[k]), side, p, units, kind))
        busy = T[xi]
    return pd.DataFrame(rows, columns=["t0", "side", "pips_units", "units", "kind"])


def report(name, tr):
    x = tr["pips_units"]
    day = tr["t0"].dt.tz_localize("UTC").dt.tz_convert(NY).dt.date
    dev = (x - x.mean()).groupby(day.to_numpy()).sum()
    t = x.mean() / (math.sqrt((dev ** 2).sum()) / len(x))
    yrs = x.groupby(tr["t0"].dt.year).sum()
    months = tr["t0"].dt.to_period("M").nunique()
    print(f"\n== {name}: baskets {len(x)}, mean {x.mean():+.2f} pips x units, t {t:+.2f}, win {(x > 0).mean():.1%}, "
          f"worst {x.min():+.0f}, $/month at 0.01/unit {x.sum() * 0.1 / months:+.2f}")
    print(f"   exits {tr['kind'].value_counts().to_dict()} | units used {tr['units'].value_counts().sort_index().to_dict()}")
    print(f"   by side: sell {x[tr.side == -1].mean():+.2f} (n={int((tr.side == -1).sum())}), buy {x[tr.side == 1].mean():+.2f}")
    print("   years:", " ".join(f"{y}:{v:+.0f}" for y, v in yrs.items()), f"| positive {(yrs > 0).sum()}/{len(yrs)}")
    return t


report("in-sample burst weeks (reference only, not the gate)", simulate([("2026-04-27", "2026-07-11")]))
oos = simulate([("2019-01-01", "2026-04-27"), ("2026-07-11", "2026-10-06")])
t = report("#431 OOS 2019-01 -> 2026-04-26 + 2026-07-11 -> 10-05 (the gate)", oos)
yrs = oos.groupby(oos["t0"].dt.year)["pips_units"].sum()
ok = oos["pips_units"].mean() > 0 and t >= 2 and (yrs > 0).sum() > len(yrs) / 2
print("verdict:", "PASS" if ok else "fail")
