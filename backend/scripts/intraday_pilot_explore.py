"""Descriptive look at the T165 pilot: volatility, bounce and continuation at the walls and flip.

**Exploration, not a test.** The pilot sessions sit inside the plan's holdout (2026-09-21 on),
and 13 sessions establish nothing. Nothing here is a trial; a pattern seen here becomes a
declared hypothesis tested on 2023-01 -> 2026-09-18.

Usage: python intraday_pilot_explore.py <out_dir> <SYMBOL> <level_events.py path>
"""

from __future__ import annotations

import importlib.util
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


def _load(path: str):
    spec = importlib.util.spec_from_file_location("level_events", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["level_events"] = mod
    spec.loader.exec_module(mod)
    return mod


SHIFTS = (0.0, -0.004, -0.002, 0.002, 0.004)


@dataclass(frozen=True)
class B:
    ts: object
    open: float
    high: float
    low: float
    close: float


def clustered(values_by_session: dict) -> str:
    """Mean of per-session means, its SE across sessions, and the counts."""
    means = [statistics.fmean(v) for v in values_by_session.values() if v]
    n = sum(len(v) for v in values_by_session.values())
    if not means:
        return "n=0"
    se = statistics.stdev(means) / math.sqrt(len(means)) if len(means) > 1 else float("nan")
    return f"{statistics.fmean(means):+7.2f} ± {se:5.2f} (n={n}, sessions={len(means)})"


def main(out: str, symbol: str, le_path: str) -> None:
    le = _load(le_path)
    folder = Path(out) / symbol
    lv = pd.concat([pd.read_parquet(p) for p in sorted(folder.glob("*.levels.parquet"))])
    lv = lv[lv["filter"] == "ALL"].sort_values("captured_at")
    bars = pd.concat([pd.read_parquet(p) for p in sorted(folder.glob("*.bars.parquet"))]).sort_values("ts")
    bars["day"] = bars.ts.dt.tz_convert("America/New_York").dt.date
    lv["day"] = lv.captured_at.dt.tz_convert("America/New_York").dt.date

    vol_side: dict[str, dict] = {"above flip": {}, "below flip": {}}
    vol_gex: dict[str, dict] = {"net GEX > 0": {}, "net GEX < 0": {}}
    events: dict[str, dict] = {}
    touches = {"put_wall": {"held": 0, "broke": 0, "neither": 0}, "call_wall": {"held": 0, "broke": 0, "neither": 0}}
    placebo = {"held": 0, "broke": 0, "neither": 0}
    inside = []

    for day, g in bars.groupby("day"):
        bl = [B(r.ts.to_pydatetime(), r.open, r.high, r.low, r.close) for r in g.itertuples()]
        caps_df = lv[lv.day == day]
        caps = [le.Capture(r.captured_at.to_pydatetime(), r.flip_point if pd.notna(r.flip_point) else None,
                           call_wall=r.call_wall if pd.notna(r.call_wall) else None,
                           put_wall=r.put_wall if pd.notna(r.put_wall) else None,
                           net_gex=r.net_gex if pd.notna(r.net_gex) else None)
                for r in caps_df.itertuples()]
        if not caps:
            continue

        # 1. volatility: |return| over the next 30 minutes (6 bars), by regime known at the bar close
        for i, b in enumerate(bl[:-6]):
            cap = le._latest(b.ts + le.BAR, caps)
            if cap is None or cap.flip is None:
                continue
            fwd = abs(math.log(bl[i + 6].close / b.close)) * 1e4
            vol_side["above flip" if b.close > cap.flip else "below flip"].setdefault(day, []).append(fwd)
            if cap.net_gex is not None:
                vol_gex["net GEX > 0" if cap.net_gex > 0 else "net GEX < 0"].setdefault(day, []).append(fwd)

        # 2. the frozen detectors, unchanged
        found = le.detect_crosses(symbol, bl, caps) + le.detect_wall_bounces(symbol, bl, caps)
        if hasattr(le, "detect_wall_breaks"):
            found += le.detect_wall_breaks(symbol, bl, caps)
        for ev in found:
            h, rc = le.horizon_returns(ev, bl)
            for k, v in [("15m", h[15]), ("30m", h[30]), ("60m", h[60]), ("close", rc)]:
                if v is not None:
                    events.setdefault((ev.key, k), {}).setdefault(day, []).append(v)

        # 3. every touch of a wall (within 0.05 %, from the near side): where is it 30 min later?
        #    Placebo: the same test at the walls shifted by +-0.2 % and +-0.4 % (no positioning there).
        for i in range(1, len(bl) - 6):
            cap = le._latest(bl[i].ts + le.BAR, caps)
            if cap is None:
                continue
            for kind, sign in (("put_wall", 1), ("call_wall", -1)):
                w0 = getattr(cap, kind)
                if w0 is None:
                    continue
                for shift in SHIFTS:
                    w = w0 * (1 + shift)
                    prev, t = bl[i - 1], bl[i]
                    ext = t.low if sign > 0 else t.high
                    if sign * (prev.close - w) > 0 and sign * (ext - w) <= le.TOUCH_TOL * w:
                        later = bl[i + 6].close
                        bucket = touches[kind] if shift == 0 else placebo
                        if sign * (later - w) < 0:
                            bucket["broke"] += 1
                        elif sign * (later - t.close) > 0:
                            bucket["held"] += 1
                        else:
                            bucket["neither"] += 1

        # 4. did the session stay between the first capture's walls?
        first = caps[0]
        if first.call_wall and first.put_wall:
            hi, lo = max(b.high for b in bl), min(b.low for b in bl)
            inside.append((day, lo >= first.put_wall and hi <= first.call_wall,
                           (first.put_wall, first.call_wall), (round(lo, 2), round(hi, 2))))

    print(f"{symbol}, {bars.day.nunique()} sessions, ALL levels. EXPLORATION, holdout sessions, not a test.\n")
    print("1. |log return| over the next 30 min, bp (per-session means, SE across sessions)")
    for name, d in {**vol_side, **vol_gex}.items():
        print(f"   {name:12s} {clustered(d)}")
    print("\n2. Frozen events, return signed in the event's direction, bp")
    for key in sorted({k for k, _ in events}):
        for hz in ("15m", "30m", "60m", "close"):
            if (key, hz) in events:
                print(f"   {key:18s} {hz:5s} {clustered(events[(key, hz)])}")
    print("\n3. Wall touches (within 0.05 % from the near side), 30 min later")
    for kind, c in [*touches.items(), ("placebo (walls +-0.2/0.4 %)", placebo)]:
        tot = sum(c.values())
        print(f"   {kind:9s} touches={tot}: held (further away than the touch close) {c['held']}, "
              f"broke (beyond the wall) {c['broke']}, in between {c['neither']}")
    print("\n4. Session range inside the first capture's walls")
    print(f"   {sum(x[1] for x in inside)}/{len(inside)} sessions stayed inside")
    for day, ok, walls, rng in inside:
        print(f"   {day} walls {walls} range {rng} {'inside' if ok else 'OUTSIDE'}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
