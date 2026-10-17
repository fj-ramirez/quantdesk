"""MAE / MFE per trade from broker M1: bars from the entry bar up to (not including) the exit bar,
plus the exit price itself. Points, long side, roll steps removed after a roll inside the trade."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, sys.argv[3])
from _common import NY, load, rolls  # noqa: E402

data, out = sys.argv[1], Path(sys.argv[2])
R = rolls(data)
for sym, f in [("S&P.fs", "s-p.fs"), ("NAS100.fs", "nas100.fs")]:
    df = load(data, sym)
    hi, lo, idx = df["high"].to_numpy(), df["low"].to_numpy(), df.index
    rl = R[R.cfd_symbol == sym]
    rt = pd.DatetimeIndex(rl.rolled_at).tz_convert(NY)
    for pre in [f"{st}{tp}-" for st in ("candle", "atr2", "swing5") for tp in ("", "-tp0.5")]:
        t = pd.read_csv(Path(data) / f"4h-desk-trend-{pre}{f}.csv")
        t0 = pd.to_datetime(t.t0, utc=True).dt.tz_convert(NY)
        t1 = pd.to_datetime(t.t1, utc=True).dt.tz_convert(NY)
        a, b = idx.searchsorted(t0), idx.searchsorted(t1)
        mae, mfe, mae_t, mfe_t = [], [], [], []
        for k in range(len(t)):
            h, l = hi[a[k]:b[k]].astype(float), lo[a[k]:b[k]].astype(float)
            ts = idx[a[k]:b[k]]
            adj = np.zeros(len(ts))
            for when, step in zip(rt, rl.step):
                if t0.iat[k] <= when < t1.iat[k]:
                    adj[ts >= when] += step * t.entry.iat[k]
            h, l = h - adj, l - adj
            ex = t.exit.iat[k] - t.roll.iat[k]
            lows, highs = np.r_[l, ex], np.r_[h, ex]
            i_lo, i_hi = lows.argmin(), highs.argmax()
            mae.append(max(0.0, t.entry.iat[k] - lows[i_lo]))
            mfe.append(max(0.0, highs[i_hi] - t.entry.iat[k]))
            tl = list(ts) + [t1.iat[k]]
            mae_t.append(tl[i_lo]); mfe_t.append(tl[i_hi])
        t["mae"], t["mfe"] = mae, mfe
        t["mae_ts"] = pd.DatetimeIndex(mae_t).tz_convert("UTC")
        t["mfe_ts"] = pd.DatetimeIndex(mfe_t).tz_convert("UTC")
        t.to_csv(out / f"4h-desk-trend-{pre}{f}.csv", index=False)
        print(sym, pre, len(t), "MAE R med", round((t.mae / t.risk).median(), 2),
              "MFE R med", round((t.mfe / t.risk).median(), 2), "MFE R mean", round((t.mfe / t.risk).mean(), 2),
              "check net<=mfe:", bool(((t.gross) <= t.mfe + 1e-6).all()), "loss>=-mae:", bool((t.gross >= -t.mae - 1e-6).all()))
