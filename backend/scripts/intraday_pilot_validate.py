"""T165 pilot acceptance: backfilled 15-minute levels and 5-minute bars against the live record.

Each live in-session ThetaData capture is matched to the latest backfill stamp at or before it,
within 15 minutes, the same "known at" rule the detectors use. Reported per filter:

* walls equal: the share of matched captures whose call wall and put wall are both identical;
* flip: the median and p90 of |flip_backfill / flip_live - 1|;
* bars: the 5-minute closes against `gex.intraday_bars` (bp), and the high/low gap.

Read-only against the database. Usage: python intraday_pilot_validate.py <out_dir> <SYMBOL>
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from app.core.db import get_engine


def main(out: str, symbol: str) -> None:
    folder = Path(out) / symbol
    bf = pd.concat([pd.read_parquet(p) for p in sorted(folder.glob("*.levels.parquet"))], ignore_index=True)
    bars = pd.concat([pd.read_parquet(p) for p in sorted(folder.glob("*.bars.parquet"))], ignore_index=True)
    start, end = bf.captured_at.min(), bf.captured_at.max() + pd.Timedelta(minutes=15)
    eng = get_engine()
    live = pd.read_sql(text("""
        SELECT s.captured_at, l.filter, l.flip_point, l.call_wall, l.put_wall, l.net_gex, l.spot
        FROM gex.snapshots s JOIN gex.gex_levels l ON l.snapshot_id = s.id
        WHERE s.underlying = :sym AND s.source = 'thetadata' AND NOT s.is_eod
          AND l.filter IN ('ALL', 'ZERO_DTE') AND s.captured_at BETWEEN :a AND :b
        ORDER BY s.captured_at"""), eng, params={"sym": symbol, "a": start, "b": end})
    live_bars = pd.read_sql(text("""
        SELECT ts, open, high, low, close FROM gex.intraday_bars
        WHERE symbol = :sym AND interval = '5m' AND ts BETWEEN :a AND :b"""),
        eng, params={"sym": symbol, "a": start, "b": end})

    print(f"{symbol}: backfill {bf.captured_at.dt.date.nunique()} sessions, "
          f"{len(bf)} level rows; live {len(live)} level rows")
    for f in ("ALL", "ZERO_DTE"):
        a = live[live["filter"] == f].sort_values("captured_at")
        b = bf[bf["filter"] == f].sort_values("captured_at")
        m = pd.merge_asof(a, b, on="captured_at", direction="backward",
                          tolerance=pd.Timedelta(minutes=15), suffixes=("_live", "_bf"))
        m = m.dropna(subset=["spot_bf"])
        both = m.dropna(subset=["call_wall_live", "put_wall_live"])
        walls = ((both.call_wall_live == both.call_wall_bf) & (both.put_wall_live == both.put_wall_bf)).mean()
        cw = (both.call_wall_live == both.call_wall_bf).mean()
        pw = (both.put_wall_live == both.put_wall_bf).mean()
        fl = (m.flip_point_bf / m.flip_point_live - 1).abs().dropna()
        sp = (m.spot_bf / m.spot_live - 1).abs()
        ng = (m.net_gex_bf / m.net_gex_live - 1).dropna()
        print(f"  {f}: matched {len(m)}/{len(a)} | both walls equal {walls:.1%} "
              f"(call {cw:.1%}, put {pw:.1%}) | flip |diff| median {fl.median():.3%} p90 {fl.quantile(.9):.3%} "
              f"(n={len(fl)}) | spot |diff| median {sp.median():.3%} | net GEX ratio-1 median {ng.median():+.1%}")
        for side in ("call_wall", "put_wall"):
            off = both[both[f"{side}_live"] != both[f"{side}_bf"]]
            if len(off):
                gap = ((off[f"{side}_bf"] - off[f"{side}_live"]).abs() / off.spot_live)
                print(f"    {side} mismatches: {len(off)}, |gap| median {gap.median():.2%} of spot, "
                      f"max {gap.max():.2%}; within 0.2% of spot: {(gap <= 0.002).mean():.0%}")

    j = bars.merge(live_bars, on="ts", suffixes=("_bf", "_live"))
    if len(j):
        c = ((j.close_bf / j.close_live - 1) * 1e4).abs()
        hi = ((j.high_live - j.high_bf) / j.close_live * 1e4)
        lo = ((j.low_bf - j.low_live) / j.close_live * 1e4)
        print(f"  bars: matched {len(j)}/{len(bars)} | close |diff| median {c.median():.2f} bp, p90 {c.quantile(.9):.2f} bp "
              f"| live high above backfill: median {hi.median():.2f} bp, p90 {hi.quantile(.9):.2f} | "
              f"live low below backfill: median {lo.median():.2f} bp, p90 {lo.quantile(.9):.2f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
