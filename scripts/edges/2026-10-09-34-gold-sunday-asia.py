"""Backlog 34: gold Asia drift on the Sunday-evening session, which #301's Mon-Thu entries never
sampled. Long the first bar at/after Sunday 18:00 NY (allowing up to 15 minutes for the weekly
open), sell Monday 03:00. Ledger test 407."""
import sys

import numpy as np
import pandas as pd

from _common import load, summarize

data = sys.argv[1]
df = load(data, "XAUUSD")
ts, op, sp = df.index, df["open"].to_numpy(), df["spread"].to_numpy()
mondays = pd.DatetimeIndex(sorted(set(ts[ts.weekday == 0].normalize())))
rows = []
for mon in mondays:
    a_t, b_t = mon - pd.Timedelta(hours=6), mon + pd.Timedelta(hours=3)
    a, b = ts.searchsorted(a_t), ts.searchsorted(b_t)
    if a >= len(ts) or b >= len(ts) or (ts[a] - a_t).total_seconds() > 900 or (ts[b] - b_t).total_seconds() > 600:
        continue
    move = op[b] - op[a]
    rows.append({"t0": ts[a], "pct": (move - 2 * sp[a]) / op[a] * 100, "gross_pct": move / op[a] * 100,
                 "cost_pct": 2 * sp[a] / op[a] * 100, "swap_pct": 0.0, "usd_per_001": move - 2 * sp[a]})
tr = pd.DataFrame(rows)
summarize("34 gold Sunday Asia", "XAUUSD", tr, 407)
print("   median spread at the weekly open: $%.2f, p90 $%.2f" % (np.median(tr["cost_pct"]) / 2 * op.mean() / 100,
                                                              np.percentile(tr["cost_pct"], 90) / 2 * op.mean() / 100))
