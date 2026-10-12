"""Observation, not a rule search: what standard indicators read on each timeframe at the
provider's entries and at their exits, next to what they read on ordinary minutes.

Each timeframe's indicators are taken from its last *completed* bar before the instant (what a
bar-close EA sees). %B and the EMA distances also get a *live* version that uses the price at the
instant against the last completed bands/averages.

    uv run --directory backend python ../scripts/edges/2026-10-12-burst-observe.py \
        data/broker-export data/broker-export/burst-entries.csv
writes data/broker-export/burst-snapshots.csv (one row per burst, entry and exit) and prints the
summary tables.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIP = 1e-4
D = Path(sys.argv[1])
m1 = pd.read_parquet(D / "eurusd.parquet").sort_values("ts").set_index("ts")
m1.index = m1.index.tz_convert(None)
m1 = m1[~m1.index.duplicated()].loc["2025-06-01":]
TFS = {"M1": "1min", "M5": "5min", "M15": "15min", "H1": "1h", "H4": "4h", "D1": "1D"}


def bars(rule: str) -> pd.DataFrame:
    if rule == "1min":
        b = m1[["open", "high", "low", "close"]].copy()
        b.index = b.index + pd.Timedelta("1min")
        return b
    if rule == "1D":  # broker day = 17:00 New York
        ny = m1.index.tz_localize("UTC").tz_convert("America/New_York")
        key = (ny - pd.Timedelta(hours=17)).normalize()
        b = m1.groupby(key.tz_localize(None)).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
        end = (b.index + pd.Timedelta(days=1) + pd.Timedelta(hours=17)).tz_localize("America/New_York", ambiguous="NaT", nonexistent="shift_forward")
        b.index = end.tz_convert("UTC").tz_localize(None)
        return b[b.index.notna()]
    b = m1.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    b.index = b.index + pd.Timedelta(rule)
    return b


def indicators(b: pd.DataFrame) -> pd.DataFrame:
    c, h, l = b["close"], b["high"], b["low"]
    out = pd.DataFrame(index=b.index)
    d = c.diff()
    out["rsi"] = 100 - 100 / (1 + d.clip(lower=0).ewm(alpha=1 / 14).mean() / (-d.clip(upper=0)).ewm(alpha=1 / 14).mean())
    ll, hh = l.rolling(14).min(), h.rolling(14).max()
    out["stoch"] = (100 * (c - ll) / (hh - ll)).rolling(3).mean()
    out["bb_mid"] = c.rolling(20).mean()
    out["bb_sd"] = c.rolling(20).std()
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / 14).mean()
    for n in (20, 50, 200):
        out[f"ema{n}"] = c.ewm(span=n).mean()
    macd = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    out["macd_hist"] = (macd - macd.ewm(span=9).mean()) / out["atr"]
    out["macd_hist_chg"] = out["macd_hist"].diff()
    up, dn = h.diff(), -l.diff()
    pdm = ((up > dn) & (up > 0)) * up
    mdm = ((dn > up) & (dn > 0)) * dn
    pdi = 100 * pdm.ewm(alpha=1 / 14).mean() / out["atr"]
    mdi = 100 * mdm.ewm(alpha=1 / 14).mean() / out["atr"]
    out["adx"] = (100 * (pdi - mdi).abs() / (pdi + mdi)).ewm(alpha=1 / 14).mean()
    out["di_diff"] = pdi - mdi
    out["candle_up"] = (c > b["open"]).astype(float)
    out["bars_since_hi20"] = 19 - h.rolling(20).apply(np.argmax, raw=True)
    out["close"] = c
    return out


IND = {k: indicators(bars(v)) for k, v in TFS.items()}


def snapshot(times: pd.DatetimeIndex, price: np.ndarray) -> pd.DataFrame:
    """Indicators as known at each instant (last completed bar), plus live %B / EMA distance."""
    cols = {}
    for tf, ind in IND.items():
        pos = ind.index.searchsorted(times, side="right") - 1
        ok = pos >= 0
        r = ind.iloc[np.where(ok, pos, 0)].reset_index(drop=True)
        r.loc[~ok] = np.nan
        cols[f"{tf}_rsi"] = r["rsi"].to_numpy()
        cols[f"{tf}_stoch"] = r["stoch"].to_numpy()
        cols[f"{tf}_pctB_live"] = ((price - (r["bb_mid"] - 2 * r["bb_sd"])) / (4 * r["bb_sd"])).to_numpy()
        for n in (20, 50, 200):
            cols[f"{tf}_ema{n}_atr_live"] = ((price - r[f"ema{n}"]) / r["atr"]).to_numpy()
        cols[f"{tf}_ema_stack"] = (np.sign(r["ema20"] - r["ema50"]) + np.sign(r["ema50"] - r["ema200"])).to_numpy()
        cols[f"{tf}_macd_hist"] = r["macd_hist"].to_numpy()
        cols[f"{tf}_macd_hist_chg"] = r["macd_hist_chg"].to_numpy()
        cols[f"{tf}_adx"] = r["adx"].to_numpy()
        cols[f"{tf}_di_diff"] = r["di_diff"].to_numpy()
        cols[f"{tf}_last_candle_up"] = r["candle_up"].to_numpy()
        cols[f"{tf}_bars_since_hi20"] = r["bars_since_hi20"].to_numpy()
    return pd.DataFrame(cols, index=times)


b = pd.read_csv(sys.argv[2], parse_dates=["ot", "ct_max"])
b["t_in"] = b["ot"] - pd.Timedelta(hours=3)
b["t_out"] = b["ct_max"] - pd.Timedelta(hours=3)
S = b[b.side == "sell"]
ent = snapshot(pd.DatetimeIndex(S["t_in"]), S["px"].to_numpy())
ext = snapshot(pd.DatetimeIndex(S["t_out"]), S["cpx"].to_numpy())

# baseline: every minute of the burst weeks, at that minute's open
per = m1[((m1.index >= "2026-04-27") & (m1.index < "2026-07-11"))]
per = per[per["spread"] > 0]
base = snapshot(per.index, per["open"].to_numpy())

pd.concat([S[["burst", "t_in", "t_out", "px", "cpx", "pips", "lots"]].reset_index(drop=True),
           ent.reset_index(drop=True).add_prefix("in_"), ext.reset_index(drop=True).add_prefix("out_")], axis=1
          ).to_csv(D / "burst-snapshots.csv", index=False)


def auc(a, c):
    a, c = a[~np.isnan(a)], c[~np.isnan(c)]
    r = pd.Series(np.concatenate([a, c])).rank().to_numpy()
    return (r[: len(a)].sum() - len(a) * (len(a) + 1) / 2) / (len(a) * len(c))


def table(snap: pd.DataFrame, label: str):
    rows = []
    for col in snap.columns:
        a, c = snap[col].to_numpy(float), base[col].to_numpy(float)
        q = np.nanpercentile(a, [10, 25, 50, 75, 90])
        rows.append((col, auc(a, c), *q, np.nanmedian(c)))
    R = pd.DataFrame(rows, columns=["feature", "auc", "p10", "p25", "med", "p75", "p90", "base_med"])
    R["tf"] = R.feature.str.split("_").str[0]
    R["ind"] = R.feature.str.split("_", n=1).str[1]
    R["edge"] = (R.auc - 0.5).abs()
    print(f"\n==== {label}: n={len(snap)}; AUC vs every minute of the burst weeks (0.5 = like a random minute)")
    piv = R.pivot(index="ind", columns="tf", values="auc")[list(TFS)]
    print(piv.round(2).to_string())
    print(f"\n-- {label}: the 25 strongest, with the spread of values at the provider's moments")
    print(R.sort_values("edge", ascending=False).head(25)[["feature", "auc", "p10", "p25", "med", "p75", "p90", "base_med"]].round(2).to_string(index=False))
    return R


pd.set_option("display.width", 220)
RE = table(ent, "SELL ENTRIES")
RX = table(ext, "SELL EXITS")


def share(snap, cond_name, f):
    a = f(snap)
    c = f(base)
    print(f"  {cond_name:48s} entries {np.nanmean(a):5.0%}   random minute {np.nanmean(c):5.0%}")


print("\n==== shares of simple conditions, entries vs random minute")
for tf in TFS:
    share(ent, f"{tf}: live price above upper Bollinger (%B > 1)", lambda s, tf=tf: s[f"{tf}_pctB_live"] > 1)
    share(ent, f"{tf}: live %B > 0.8", lambda s, tf=tf: s[f"{tf}_pctB_live"] > 0.8)
    share(ent, f"{tf}: RSI > 70", lambda s, tf=tf: s[f"{tf}_rsi"] > 70)
    share(ent, f"{tf}: RSI > 60", lambda s, tf=tf: s[f"{tf}_rsi"] > 60)
    share(ent, f"{tf}: stoch > 80", lambda s, tf=tf: s[f"{tf}_stoch"] > 80)
    share(ent, f"{tf}: EMAs stacked bearish (20<50<200)", lambda s, tf=tf: s[f"{tf}_ema_stack"] == -2)
    share(ent, f"{tf}: price above EMA50", lambda s, tf=tf: s[f"{tf}_ema50_atr_live"] > 0)
    share(ent, f"{tf}: MACD hist falling", lambda s, tf=tf: s[f"{tf}_macd_hist_chg"] < 0)
print("\n==== exits: where was price when the basket closed")
for tf in ("M1", "M5", "M15", "H1"):
    share(ext, f"{tf}: live %B < 0.2 (near lower band)", lambda s, tf=tf: s[f"{tf}_pctB_live"] < 0.2)
    share(ext, f"{tf}: live %B < 0.5 (below middle)", lambda s, tf=tf: s[f"{tf}_pctB_live"] < 0.5)
    share(ext, f"{tf}: RSI < 30", lambda s, tf=tf: s[f"{tf}_rsi"] < 30)
    share(ext, f"{tf}: stoch < 20", lambda s, tf=tf: s[f"{tf}_stoch"] < 20)

# ---- per-burst view of the readings that stood out, plus buys and winners/losers
key_in = ["D1_ema_stack", "D1_ema50_atr_live", "H4_ema_stack", "H1_pctB_live", "M15_pctB_live", "M15_rsi", "M5_rsi", "M5_stoch",
          "M15_ema50_atr_live", "H1_macd_hist_chg"]
key_out = ["M5_stoch", "M5_pctB_live", "M5_rsi", "M15_pctB_live", "M5_macd_hist"]
Bu = b[b.side == "buy"]
bent = snapshot(pd.DatetimeIndex(Bu["t_in"]), Bu["px"].to_numpy())
print("\n==== the buys (mirror check: is the daily trend up when they buy?)")
print(pd.concat([Bu[["burst", "t_in", "pips"]].reset_index(drop=True), bent[key_in].reset_index(drop=True)], axis=1).round(2).to_string(index=False))
tab = pd.concat([S[["burst", "t_in", "pips"]].reset_index(drop=True), ent[key_in].reset_index(drop=True).add_prefix("in:"),
                 ext[key_out].reset_index(drop=True).add_prefix("out:")], axis=1)
print("\n==== every sell, entry and exit readings")
print(tab.round(2).to_string(index=False))
w = tab["pips"] > 0
print("\n==== winners vs losers (medians)")
print(pd.DataFrame({"winners": tab[w].median(numeric_only=True), "losers": tab[~w].median(numeric_only=True)}).round(2).to_string())
