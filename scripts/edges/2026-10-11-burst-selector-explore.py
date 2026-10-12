"""Exploration (not a test): what selects the provider's EURUSD burst entries?

Sample: docs/edges/eurusd-burst-fade-observations.csv (72 bursts). Everything here is a search
on the sample, and every feature counts toward the search count in eurusd-burst-fade.md.

    uv run --directory backend python ../scripts/edges/2026-10-11-burst-selector-explore.py \
        data/broker-export ../docs/edges/eurusd-burst-fade-observations.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIP = 1e-4
df = pd.read_parquet(Path(sys.argv[1]) / "eurusd.parquet").sort_values("ts").set_index("ts")
df.index = df.index.tz_convert(None)
obs = pd.read_csv(sys.argv[2])
obs["open_ts_utc"] = pd.to_datetime(obs["open_ts_utc"], format="mixed")
obs["dir"] = np.where(obs["direction"] == "buy", 1, -1)
obs["k"] = df.index.searchsorted(obs["open_ts_utc"].dt.floor("min"))

c = df["close"].to_numpy()
o = df["open"].to_numpy()

# ---- 1. event study: mean signed path (pips, + = in the trade's favour) around the entry ----
offs = [-240, -120, -60, -30, -15, -10, -5, -3, -1, 0, 1, 3, 5, 10, 15, 30, 60, 120, 240]
P = []
for r in obs.itertuples():
    ref = o[r.k]
    P.append([(c[r.k + d] - ref) / PIP * r.dir if d != 0 else 0.0 for d in offs])
P = pd.DataFrame(P, columns=offs)
s = obs["dir"] == -1
print("== event study, sells (n=%d): median signed pips vs entry-bar open" % s.sum())
print(P[s].median().round(1).to_string())
print("share of sells where price is in favour at +t:")
print((P[s] > 0).mean().round(2).to_string())


# ---- 2. features at every minute (known at the entry bar's open), AUC of sell entries vs all ----
def features(d: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=d.index)
    op, cl, hi, lo = d["open"], d["close"], d["high"], d["low"]
    pc = cl.shift(1)  # last completed close
    for n in (1, 3, 5, 10, 15, 30, 60, 120, 240, 480, 1440):
        f[f"ret{n}"] = (op - cl.shift(n + 1)) / PIP
    for n in (15, 60, 240, 1440):
        hh, ll = hi.shift(1).rolling(n).max(), lo.shift(1).rolling(n).min()
        f[f"pos{n}"] = (op - ll) / (hh - ll)
        f[f"dhi{n}"] = (hh - op) / PIP
        f[f"since_hi{n}"] = n - hi.shift(1).rolling(n).apply(np.argmax, raw=True)
    for n in (20, 60, 240, 720):
        f[f"z{n}"] = (pc - pc.rolling(n).mean()) / pc.rolling(n).std()
    for tf, n in (("1min", 14), ("5min", 14), ("15min", 14), ("1h", 14)):
        x = cl.resample(tf, label="right", closed="right").last().dropna()
        dlt = x.diff()
        rsi = 100 - 100 / (1 + dlt.clip(lower=0).ewm(alpha=1 / n).mean() / (-dlt.clip(upper=0)).ewm(alpha=1 / n).mean())
        f[f"rsi_{tf}"] = rsi.reindex(d.index, method="ffill").shift(1)
    tr = (hi - lo) / PIP
    f["atr_ratio"] = tr.shift(1).rolling(15).mean() / tr.shift(1).rolling(240).mean()
    f["tv_ratio"] = d["tick_volume"].shift(1).rolling(5).mean() / d["tick_volume"].shift(1).rolling(240).mean()
    f["spread"] = d["spread"]
    f["hour"] = d.index.hour + d.index.minute / 60
    f["minute"] = d.index.minute
    f["dow"] = d.index.dayofweek
    f["round50"] = ((op / PIP) % 50).where(lambda v: v < 25, lambda v: v - 50)  # pips above nearest 50
    f["round10"] = ((op / PIP) % 10).where(lambda v: v < 5, lambda v: v - 10)
    day = (d.index - pd.Timedelta(hours=21)).normalize()  # ~17:00 New York rollover
    dd = d.groupby(day).agg(h=("high", "max"), l=("low", "min"), c=("close", "last"))
    pdh = dd["h"].shift(1).reindex(day).to_numpy()
    pdl = dd["l"].shift(1).reindex(day).to_numpy()
    f["d_pdh"] = (pdh - op.to_numpy()) / PIP
    f["d_pdl"] = (op.to_numpy() - pdl) / PIP
    for n in (5, 20, 50):
        sma = dd["c"].rolling(n).mean().shift(1).reindex(day).to_numpy()
        f[f"d_sma{n}d"] = (op.to_numpy() - sma) / PIP
    # intraday: today's high so far
    th = hi.shift(1).groupby(day).cummax()
    tl = lo.shift(1).groupby(day).cummin()
    f["pos_today"] = (op - tl) / (th - tl)
    return f


win = df.loc["2026-03-01":]
F = features(win)
period = ((F.index >= "2026-04-27") & (F.index < "2026-07-11")) | ((F.index >= "2026-10-05") & (F.index < "2026-10-09"))
base = F[period & (F["spread"] > 0)]
ent = F.loc[obs.loc[s, "open_ts_utc"].dt.floor("min")]


def auc(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    r = pd.Series(np.concatenate([a, b])).rank().to_numpy()
    return (r[: len(a)].sum() - len(a) * (len(a) + 1) / 2) / (len(a) * len(b))


rows = []
for col in F.columns:
    a, b = ent[col].to_numpy(float), base[col].to_numpy(float)
    rows.append((col, auc(a, b), np.nanmedian(a), np.nanmedian(b), np.nanpercentile(a, 10), np.nanpercentile(a, 90)))
R = pd.DataFrame(rows, columns=["feature", "auc", "med_entry", "med_all", "p10_entry", "p90_entry"])
R["edge"] = (R["auc"] - 0.5).abs()
print(f"\n== AUC, sell entries (n={len(ent)}) vs every minute in the burst periods (n={len(base)}); {len(F.columns)} features")
print(R.sort_values("edge", ascending=False).round(3).to_string(index=False))


# ---- 3. second level: entries vs minutes that were already stretched up in a daily downtrend ----
elig = base[(base["z720"] >= 1.0) & (base["d_sma20d"] < 0)]
ent2 = ent[(ent["z720"] >= 1.0) & (ent["d_sma20d"] < 0)]
print(f"\n== entries meeting z720>=1 & below 20d SMA: {len(ent2)}/{len(ent)}; eligible minutes {len(elig)} "
      f"({len(elig) / len(base):.1%} of all), eligible days {elig.index.normalize().nunique()}")
rows = []
for col in F.columns:
    a, b = ent2[col].to_numpy(float), elig[col].to_numpy(float)
    rows.append((col, auc(a, b), np.nanmedian(a), np.nanmedian(b)))
R2 = pd.DataFrame(rows, columns=["feature", "auc", "med_entry", "med_elig"])
R2["edge"] = (R2["auc"] - 0.5).abs()
print(R2.sort_values("edge", ascending=False).head(20).round(3).to_string(index=False))
dtrend = ent["d_sma20d"] < 0
print("\nsells below the 20d SMA:", f"{dtrend.mean():.0%}", "| all minutes below:", f"{(base['d_sma20d'] < 0).mean():.0%}")
bo = F.loc[obs.loc[~s, "open_ts_utc"].dt.floor("min")]
print("buys:", bo[["z720", "ret480", "pos1440", "d_sma20d", "d_sma50d", "rsi_15min"]].round(2).to_string())


# ---- 4. in-sample replication: does a score on these features reproduce the provider's +4 pips? ----
ts_all = df.index.to_numpy()
lo_, hi_, op_, sp_ = (df[k].to_numpy(float) for k in ("low", "high", "open", "spread"))
spx_ = sp_ * 1e-5


def exit_pips(k: int, side: int, stop=25, on=5, trail=3) -> tuple[float, int]:
    j = np.searchsorted(ts_all, ts_all[k] + np.timedelta64(24, "h"))
    seg = slice(k, j)
    if side == -1:
        e = op_[k]
        best, worst, ao = e - (lo_[seg] + spx_[seg]), e - (hi_[seg] + spx_[seg]), e - (op_[seg] + spx_[seg])
    else:
        e = op_[k] + spx_[k]
        best, worst, ao = hi_[seg] - e, lo_[seg] - e, op_[seg] - e
    bb = np.maximum(np.concatenate([[0], np.maximum.accumulate(best)[:-1]]), 0)
    sh = worst <= -stop * PIP
    lvl = bb - trail * PIP
    th = (bb >= on * PIP) & (worst <= lvl)
    fs = np.argmax(sh) if sh.any() else 10**9
    ft = np.argmax(th) if th.any() else 10**9
    if fs == ft == 10**9:
        return ((op_[j] - e) if side == 1 else e - (op_[j] + spx_[j])) / PIP, j
    if fs <= ft:
        return min(-stop * PIP, ao[fs]) / PIP, k + fs
    return min(lvl[ft], ao[ft]) / PIP, k + ft


SCORE_UP = ["z720", "ret120", "ret480", "pos1440", "rsi_15min"]
SCORE_DOWN = ["since_hi15"]


def score(frame: pd.DataFrame, ref: pd.DataFrame) -> pd.Series:
    parts = []
    for col in SCORE_UP + SCORE_DOWN:
        r = np.searchsorted(np.sort(ref[col].dropna().to_numpy()), frame[col].to_numpy()) / ref[col].notna().sum()
        parts.append(1 - r if col in SCORE_DOWN else r)
    return pd.Series(np.nanmean(parts, axis=0), index=frame.index)


sc = score(base, base)
print(f"\n== in-sample: score = mean percentile of {SCORE_UP} and (low) {SCORE_DOWN}")
print("provider sell entries' score: median", round(float(score(ent, base).median()), 3), "| all minutes 0.5")
pos_index = {t: i for i, t in enumerate(ts_all[np.searchsorted(ts_all, base.index[0].to_datetime64()):])}
off = np.searchsorted(ts_all, base.index[0].to_datetime64())
for q in (0.90, 0.95, 0.98, 0.99, 0.995):
    thr = sc.quantile(q)
    cand = sc[(sc >= thr) & (base["spread"] <= 15)].index
    res, busy = [], np.datetime64("1970-01-01", "ns")
    for t in cand:
        tt = t.to_datetime64()
        if tt < busy:
            continue
        k = np.searchsorted(ts_all, tt)
        p, xi = exit_pips(k, -1)
        res.append(p)
        busy = ts_all[xi]
    res = np.array(res)
    print(f"  top {1 - q:.1%} minutes (score >= {thr:.3f}): trades {len(res)}, mean {res.mean():+.2f} pips (1x spread), win {np.mean(res > 0):.0%}")


# ---- 5. what separates the provider's entries from equally-scored minutes? cross-asset + clock ----
def xret(sym: str, idx: pd.DatetimeIndex, n: int) -> np.ndarray:
    x = pd.read_parquet(Path(sys.argv[1]) / f"{sym}.parquet").set_index("ts")["close"]
    x.index = x.index.tz_convert(None)
    x = x[~x.index.duplicated()].sort_index()
    a = x.reindex(idx - pd.Timedelta(minutes=1), method="ffill").to_numpy()
    b = x.reindex(idx - pd.Timedelta(minutes=n + 1), method="ffill").to_numpy()
    return (a / b - 1) * 1e4  # bps


hs = base[sc >= sc.quantile(0.90)].copy()
hs = hs[hs.index < "2026-10-01"]  # cross-asset exports end 2026-10-09; keep to the statement period
pe = ent[ent.index < "2026-10-01"].copy()
for frame in (hs, pe):
    frame["eur120_bps"] = frame["ret120"] * PIP / 1.15 * 1e4
    for sym, tag in (("xauusd", "xau"), ("s-p.fs", "spx")):
        for n in (30, 120, 480):
            frame[f"{tag}{n}"] = xret(sym, frame.index, n)
    frame["xau_vs_eur120"] = frame["xau120"] - frame["eur120_bps"]
# outcome of each high-score minute (sell, frozen exit) to see which splits matter for P&L
hs["pnl"] = [exit_pips(np.searchsorted(ts_all, t.to_datetime64()), -1)[0] for t in hs.index]
pe["pnl"] = [exit_pips(np.searchsorted(ts_all, t.to_datetime64()), -1)[0] for t in pe.index]
cols = ["xau30", "xau120", "xau480", "spx30", "spx120", "spx480", "xau_vs_eur120", "hour", "dow", "minute",
        "atr_ratio", "tv_ratio", "d_sma20d", "d_sma5d", "d_pdh", "round50", "ret5", "ret1440"]
print(f"\n== provider entries (n={len(pe)}) vs top-10% score minutes (n={len(hs)}), statement period")
print(f"   pnl: provider {pe['pnl'].mean():+.2f}, top-10% minutes {hs['pnl'].mean():+.2f}")
rows = []
for col in cols:
    a, b = pe[col].to_numpy(float), hs[col].to_numpy(float)
    q = hs[col] >= hs[col].median()
    rows.append((col, auc(a, b), np.nanmedian(a), np.nanmedian(b), hs.loc[q, "pnl"].mean(), hs.loc[~q, "pnl"].mean()))
R3 = pd.DataFrame(rows, columns=["feature", "auc_vs_hs", "med_entry", "med_hs", "pnl_hs_above_med", "pnl_hs_below_med"])
R3["edge"] = (R3["auc_vs_hs"] - 0.5).abs()
print(R3.sort_values("edge", ascending=False).round(2).to_string(index=False))


# ---- 6. in-sample: explicit "mature stretch + stall + inside yesterday's range" rules, one at a time ----
def sim_rule(frame: pd.DataFrame, mask_sell: pd.Series, mask_buy: pd.Series | None = None) -> np.ndarray:
    sig = pd.Series(0, index=frame.index)
    sig[mask_sell] = -1
    if mask_buy is not None:
        sig[mask_buy & ~mask_sell] = 1
    sig = sig[sig != 0]
    res, busy = [], np.datetime64("1970-01-01", "ns")
    for t, side in sig.items():
        tt = t.to_datetime64()
        if tt < busy:
            continue
        p, xi = exit_pips(np.searchsorted(ts_all, tt), int(side))
        res.append(p)
        busy = ts_all[xi]
    return np.array(res)


B0 = base
common = (B0["spread"] <= 10) & (B0["hour"] >= 7) & (B0["hour"] < 20)
variants = {
    "V1 z720>=1.5 & ret120>=10 & ret5<=0":
        (B0["z720"] >= 1.5) & (B0["ret120"] >= 10) & (B0["ret5"] <= 0),
    "V2 V1 & since_hi15<=5":
        (B0["z720"] >= 1.5) & (B0["ret120"] >= 10) & (B0["ret5"] <= 0) & (B0["since_hi15"] <= 5),
    "V3 V1 & below prev-day high":
        (B0["z720"] >= 1.5) & (B0["ret120"] >= 10) & (B0["ret5"] <= 0) & (B0["d_pdh"] > 0),
    "V4 V3 & ret1440<=20":
        (B0["z720"] >= 1.5) & (B0["ret120"] >= 10) & (B0["ret5"] <= 0) & (B0["d_pdh"] > 0) & (B0["ret1440"] <= 20),
}
print("\n== in-sample explicit rules (sells only, one position at a time, 07-20 UTC, spread <= 1 pip)")
for name, m in variants.items():
    r = sim_rule(B0, m & common)
    print(f"  {name:40s} n={len(r):4d} mean {r.mean():+.2f} win {np.mean(r > 0):.0%}")


# ---- 7. in-sample: EUR-specific rallies (gold did not rally with EUR) ----
B7 = base[base.index < "2026-10-01"].copy()
B7["xau120"] = xret("xauusd", B7.index, 120)
B7["eur120_bps"] = B7["ret120"] * PIP / 1.15 * 1e4
c7 = (B7["spread"] <= 10) & (B7["hour"] >= 7) & (B7["hour"] < 20)
v1 = (B7["z720"] >= 1.5) & (B7["ret120"] >= 10) & (B7["ret5"] <= 0)
for name, m in {"V5 V1 & xau120 < eur120 (EUR-specific)": v1 & (B7["xau120"] < B7["eur120_bps"]),
                "V6 V1 & xau120 < 0.5*eur120": v1 & (B7["xau120"] < 0.5 * B7["eur120_bps"])}.items():
    r = sim_rule(B7, m & c7)
    print(f"  {name:40s} n={len(r):4d} mean {r.mean():+.2f} win {np.mean(r > 0):.0%}")
pe7 = pe.copy()
print("provider entries with xau120 < eur120:", f"{(pe7['xau120'] < pe7['eur120_bps']).mean():.0%}",
      "| top-10% minutes:", f"{(hs['xau120'] < hs['eur120_bps']).mean():.0%}")
