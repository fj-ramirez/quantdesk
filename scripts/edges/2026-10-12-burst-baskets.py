"""Loop round 3: the provider as a grid. Group bursts into baskets (bursts whose open
intervals overlap), then measure add spacing, add sizing and the basket exit.

    uv run --directory backend python ../scripts/edges/2026-10-12-burst-baskets.py data/broker-export/burst-entries.csv
"""
import sys

import numpy as np
import pandas as pd

b = pd.read_csv(sys.argv[1], parse_dates=["ot", "ct_min", "ct_max"]).sort_values("ot")
b = b[b.side == "sell"].reset_index(drop=True)
basket, end, cur = [], pd.Timestamp.min, -1
for r in b.itertuples():
    if r.ot > end:
        cur += 1
        end = r.ct_max
    else:
        end = max(end, r.ct_max)
    basket.append(cur)
b["basket"] = basket
rows = []
for k, g in b.groupby("basket"):
    lots = g.lots.to_numpy()
    px = g.px.to_numpy()
    avg = (px * lots).sum() / lots.sum()
    cpx = (g.cpx * g.lots).sum() / g.lots.sum()
    adds = np.diff(px) / 1e-4
    rows.append({"basket": k, "start": g.ot.min(), "n_bursts": len(g), "first_px": px[0], "lots": ",".join(f"{x:.2f}" for x in lots),
                 "add_gaps_pips": ",".join(f"{x:+.0f}" for x in adds), "add_vs_first_max": (px.max() - px[0]) / 1e-4,
                 "avg_px": avg, "close_px": cpx, "basket_pips_vs_avg": (avg - cpx) / 1e-4,
                 "basket_pips_vs_first": (px[0] - cpx) / 1e-4, "net_usd": g.net.sum(),
                 "close_spread_min": (g.ct_max.max() - g.ct_min.min()).total_seconds() / 60,
                 "hours": (g.ct_max.max() - g.ot.min()).total_seconds() / 3600})
R = pd.DataFrame(rows)
pd.set_option("display.width", 250)
print(R.round(2).to_string(index=False))
m = R[R.n_bursts > 1]
print(f"\nbaskets {len(R)}, multi-burst {len(m)}; adds per multi basket {m.n_bursts.mean() - 1:.1f}")
gaps = np.concatenate([np.array([float(x) for x in s.split(",")]) for s in m.add_gaps_pips if s])
print("add gaps (pips, + = added higher, against the short):", np.round(np.sort(gaps), 0).tolist())
print("basket result vs avg price (pips): single", R[R.n_bursts == 1].basket_pips_vs_avg.round(1).tolist())
print("                                  multi ", m.basket_pips_vs_avg.round(1).tolist())
