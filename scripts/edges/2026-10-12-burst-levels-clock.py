"""Loop round 2 exploration: are the provider's sells anchored to price levels or the clock?
Levels: floor pivots (P, R1, R2, S1 from the prior NY day's HLC), the NY-day open, VWAP of the
NY day (tick-volume weighted) and its 1/2-sd bands. Clock: minutes after 08:30 and 10:00 NY.

    uv run --directory backend python ../scripts/edges/2026-10-12-burst-levels-clock.py data/broker-export ../docs/edges/eurusd-burst-fade-observations.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIP = 1e-4
df = pd.read_parquet(Path(sys.argv[1]) / "eurusd.parquet").sort_values("ts").set_index("ts")
df = df[~df.index.duplicated()].loc["2026-03-01":]
ny = df.index.tz_convert("America/New_York")
day = (ny - pd.Timedelta(hours=17)).normalize()  # NY trading day starting 17:00
df["day"] = day.tz_localize(None)
g = df.groupby("day")
dd = g.agg(h=("high", "max"), l=("low", "min"), c=("close", "last"), o=("open", "first"))
prev = dd.shift(1)
P = (prev.h + prev.l + prev.c) / 3
lv = pd.DataFrame({"P": P, "R1": 2 * P - prev.l, "R2": P + (prev.h - prev.l), "S1": 2 * P - prev.h,
                   "PDH": prev.h, "DO": dd.o})
L = lv.reindex(df["day"]).set_index(df.index)
tp = (df.high + df.low + df.close) / 3
w = df.tick_volume.clip(lower=1)
cw = (tp * w).groupby(df["day"]).cumsum()
cv = w.groupby(df["day"]).cumsum()
vwap = cw / cv
var = ((tp - vwap) ** 2 * w).groupby(df["day"]).cumsum() / cv
F = pd.DataFrame(index=df.index)
F["px"] = df.open
for k in lv.columns:
    F[f"d_{k}"] = (df.open - L[k]) / PIP
F["vwap_z"] = (df.open - vwap.shift(1)) / np.sqrt(var.shift(1))
nyt = ny.hour * 60 + ny.minute
F["min_after_830"] = nyt - 510
F["min_after_1000"] = nyt - 600
F["ny_minute_of_day"] = nyt
F.index = F.index.tz_convert(None)

obs = pd.read_csv(sys.argv[2])
obs["t"] = pd.to_datetime(obs["open_ts_utc"], format="mixed").dt.floor("min")
sells = obs.loc[obs.direction == "sell", "t"]
per = ((F.index >= "2026-04-27") & (F.index < "2026-07-11")) | ((F.index >= "2026-10-05") & (F.index < "2026-10-09"))
base, ent = F[per], F.loc[sells]


def auc(a, b):
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    r = pd.Series(np.concatenate([a, b])).rank().to_numpy()
    return (r[: len(a)].sum() - len(a) * (len(a) + 1) / 2) / (len(a) * len(b))


print("feature            auc   med_entry  med_all  |  |dist|<=3 pips: entries vs all")
for c in [c for c in F.columns if c != "px"]:
    a, b = ent[c].to_numpy(float), base[c].to_numpy(float)
    near = f"{np.mean(np.abs(a) <= 3):.2f} vs {np.mean(np.abs(b) <= 3):.2f}" if c.startswith("d_") else ""
    print(f"{c:16s} {auc(a, b):.3f} {np.nanmedian(a):9.1f} {np.nanmedian(b):9.1f}  | {near}")
print("\nNY clock of the sells (hour histogram):")
print(pd.Series(ent["ny_minute_of_day"] // 60).value_counts().sort_index().to_dict())
print("all minutes, same hours share:", (pd.Series(base["ny_minute_of_day"] // 60).value_counts(normalize=True).sort_index() * len(ent)).round(1).to_dict())
