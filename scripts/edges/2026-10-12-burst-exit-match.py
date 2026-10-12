"""Observation: which standard exit rule reproduces the provider's actual close *times*?

For every burst with a known close (April-July statement + October screenshots), each candidate
rule is run from the burst's entry; its predicted close time is compared with the real one.
Price-touch rules use the M1 high/low against the level of the timeframe's last *completed* bar;
indicator rules fire at the completed bar's close. Basket closes (several bursts closed together)
make some real closes later than any per-burst rule; that is reported, not hidden.

    uv run --directory backend python ../scripts/edges/2026-10-12-burst-exit-match.py data/broker-export
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIP, PT = 1e-4, 1e-5
D = Path(sys.argv[1])
m1 = pd.read_parquet(D / "eurusd.parquet").sort_values("ts").set_index("ts")
m1.index = m1.index.tz_convert(None)
m1 = m1[~m1.index.duplicated()].loc["2026-03-01":]
T = m1.index.to_numpy().astype("datetime64[ns]")
CT = T + np.timedelta64(1, "m")
O, H, L, C, SP = (m1[k].to_numpy(float) for k in ("open", "high", "low", "close", "spread"))
ASK_H, ASK_L = H + SP * PT, L + SP * PT


def bars(rule):
    b = m1.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    b.index = b.index + pd.Timedelta(rule)
    return b


def asof(s):
    idx = s.index.to_numpy().astype("datetime64[ns]")
    pos = np.searchsorted(idx, CT, side="right") - 1
    v = s.to_numpy(float)[np.clip(pos, 0, None)]
    v[pos < 0] = np.nan
    return v


def prev_asof(s):
    """Level from the last completed bar *before* the current M1 bar starts (for intrabar touches)."""
    idx = s.index.to_numpy().astype("datetime64[ns]")
    pos = np.searchsorted(idx, T, side="right") - 1
    v = s.to_numpy(float)[np.clip(pos, 0, None)]
    v[pos < 0] = np.nan
    return v


IND = {}
for tf, r in (("M1", "1min"), ("M5", "5min"), ("M15", "15min"), ("H1", "1h")):
    b = bars(r)
    c = b["close"]
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    ll, hh = b["low"].rolling(14).min(), b["high"].rolling(14).max()
    d = c.diff()
    IND[tf] = {
        "up": prev_asof(mid + 2 * sd), "dn": prev_asof(mid - 2 * sd), "mid": prev_asof(mid),
        "stoch": asof((100 * (c - ll) / (hh - ll)).rolling(3).mean()),
        "rsi": asof(100 - 100 / (1 + d.clip(lower=0).ewm(alpha=1 / 14).mean() / (-d.clip(upper=0)).ewm(alpha=1 / 14).mean())),
    }


def rules(side):
    """name -> boolean array 'exit fires on this M1 bar' for a position of `side`."""
    out = {}
    for tf in ("M1", "M5", "M15", "H1"):
        I = IND[tf]
        if side == -1:
            out[f"{tf} touch lower band"] = L <= I["dn"]
            out[f"{tf} touch middle band"] = L <= I["mid"]
            out[f"{tf} stoch < 20"] = I["stoch"] < 20
            out[f"{tf} RSI < 30"] = I["rsi"] < 30
        else:
            out[f"{tf} touch upper band"] = H >= I["up"]
            out[f"{tf} touch middle band"] = H >= I["mid"]
            out[f"{tf} stoch > 80"] = I["stoch"] > 80
            out[f"{tf} RSI > 70"] = I["rsi"] > 70
    return out


obs = pd.read_csv(D / "burst-entries.csv", parse_dates=["ot", "ct_max", "ct_min"])
obs = obs.rename(columns={"side": "dir"})
ev = pd.DataFrame({"t_in": obs["ot"] - pd.Timedelta(hours=3), "t_out": obs["ct_max"] - pd.Timedelta(hours=3),
                   "t_out_first": obs["ct_min"] - pd.Timedelta(hours=3),
                   "side": np.where(obs["dir"] == "buy", 1, -1), "px": obs["px"], "pips": obs["pips"], "src": "Apr-Jul"})
octo = pd.DataFrame({"t_in": pd.to_datetime(["2026-10-07 15:38:29", "2026-10-08 16:00:07"]),
                     "t_out": pd.to_datetime(["2026-10-07 17:53:32", "2026-10-08 17:15:38"]),
                     "side": [1, 1], "px": [1.11869, 1.11915], "pips": [8.5, 16.1], "src": "Oct"})
octo["t_out_first"] = octo["t_out"]
ev = pd.concat([ev, octo], ignore_index=True)
ev = ev[ev["pips"] > 0].reset_index(drop=True)  # winners: losers were stopped/cut, a different exit
R = {}
for side in (-1, 1):
    rs = rules(side)
    sub = ev[ev.side == side]
    for i, e in sub.iterrows():
        k = np.searchsorted(T, np.datetime64(e.t_in.floor("min"), "ns"))
        j_end = np.searchsorted(T, np.datetime64(e.t_in + pd.Timedelta(hours=36), "ns"))
        k1 = k + 1  # the entry minute itself does not count
        for name, fire in list(rs.items()) + [("fixed TP 5 pips", None), ("fixed TP 10 pips", None), ("trail 5/3", None)]:
            if fire is None:
                if side == -1:
                    best = e.px - ASK_L[k1:j_end]
                else:
                    best = H[k1:j_end] - e.px
                if name.startswith("fixed"):
                    tp = (5 if "5 " in name else 10) * PIP
                    hit = best >= tp
                else:
                    worst = (e.px - ASK_H[k1:j_end]) if side == -1 else (L[k1:j_end] - e.px)
                    bb = np.maximum(np.concatenate([[0], np.maximum.accumulate(best)[:-1]]), 0)
                    hit = (bb >= 5 * PIP) & (worst <= bb - 3 * PIP)
            else:
                hit = np.nan_to_num(fire[k1:j_end], nan=0).astype(bool)
            j = k1 + int(np.argmax(hit)) if hit.any() else None
            pred = pd.Timestamp(CT[j]) if j is not None else pd.NaT
            err = (pred - e.t_out).total_seconds() / 60 if j is not None else np.nan
            err_first = (pred - e.t_out_first).total_seconds() / 60 if j is not None else np.nan
            R.setdefault(name, []).append((e.src, side, err, min(abs(err), abs(err_first)) if j is not None else np.nan))

rows = []
for name, v in R.items():
    a = pd.DataFrame(v, columns=["src", "side", "err", "abs_best"])
    rows.append({"rule": name, "n": len(a), "within_10min": (a["abs_best"] <= 10).mean(), "within_30min": (a["abs_best"] <= 30).mean(),
                 "median_err_min": a["err"].median(), "fires_early_share": (a["err"] < -10).mean(),
                 "oct_within_10": (a.loc[a.src == "Oct", "abs_best"] <= 10).mean() if (a.src == "Oct").any() else np.nan})
out = pd.DataFrame(rows).sort_values("within_10min", ascending=False)
print(f"winning bursts with a known close: {len(ev)} (sells {int((ev.side == -1).sum())}, buys {int((ev.side == 1).sum())})")
print("err = predicted - actual close (min); within = |err| <= N vs the burst's first or last order close")
pd.set_option("display.width", 200)
print(out.round(2).to_string(index=False))
