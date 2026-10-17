"""nas-4h-atr3 (T180): the executor's rule against backtest #562, trade for trade.

Replays `Nas4hAtr3.signal` bin by bin over the broker M1 export, with the broker's intrabar stop
simulated on M1 lows as the backtest does, and requires the same trades as
`scripts/edges/_desk4h.simulate(..., "atr3")`. The export is local data (`data/broker-export`,
not in git), so the test skips where it is absent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.modules.broker.strategies import Held, Nas4hAtr3

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "backend" / "data" / "broker-export"
pytestmark = pytest.mark.skipif(not (DATA / "nas100.fs.parquet").exists(),
                                reason="broker M1 export not present")
S = Nas4hAtr3()


def replay(m1: pd.DataFrame) -> pd.DataFrame:
    """The class's decisions over history: flat -> open at the next M1 open with
    `initial_stop`; long -> broker stop on M1 lows, then `signal` at each bin close."""
    h4 = S.h4_bars(m1)
    wall = m1.index.tz_convert("America/New_York").tz_localize(None)
    b = (wall - pd.Timedelta(hours=17)).floor("4h") + pd.Timedelta(hours=17)
    pos = pd.Series(np.arange(len(m1)), index=b)
    first, last = pos.groupby(level=0).min(), pos.groupby(level=0).max()
    o, lo, sp = m1["open"].to_numpy(), m1["low"].to_numpy(), m1["spread"].to_numpy()
    ts = m1.index
    out, held, i, n = [], None, 200, len(h4)
    stop = None
    while i < n - 1:
        if held is None:
            action, _, anchor = S.signal(h4.iloc[: i + 1], None)
            if action != "open":
                i += 1
                continue
            e = last.iloc[i] + 1
            if e >= len(m1):
                break
            entry, s = o[e], sp[e]
            stop = S.initial_stop(anchor, 0.0, s)
            if entry <= stop:
                out.append((ts[e], ts[e], entry, entry))
                i += 1
                continue
            held, t0, j = Held(ts[e].to_pydatetime(), entry, stop), ts[e], i + 1
            while j < n:
                a, z = max(first.iloc[j], e), last.iloc[j] + 1
                hit = np.nonzero(lo[a:z] <= held.stop)[0]
                if len(hit):
                    m = a + hit[0]
                    out.append((t0, ts[m], entry, min(o[m], held.stop) if m > e else held.stop))
                    break
                action, _, px = S.signal(h4.iloc[: j + 1], held)
                if action == "close":
                    if z < len(m1):
                        out.append((t0, ts[z], entry, o[z]))
                    break
                if action == "trail":
                    held = Held(held.entry_at, held.entry, px)
                j += 1
            held, i = None, max(j, i + 1)
    return pd.DataFrame(out, columns=["t0", "t1", "entry", "exit"])


def test_class_reproduces_backtest_562():
    sys.path.insert(0, str(ROOT / "scripts" / "edges"))
    from _common import load
    from _desk4h import signal_mask, simulate

    bt = simulate(str(DATA), "NAS100.fs", signal_mask(str(DATA), "NAS100.fs"), "atr3")
    df = load(str(DATA), "NAS100.fs")
    m1 = df.set_index(pd.DatetimeIndex(df["ts"]))[["open", "high", "low", "close", "spread"]]
    got = replay(m1)
    assert len(got) == len(bt)
    assert (got.t0.to_numpy() == pd.DatetimeIndex(bt.t0).tz_convert("UTC").to_numpy()).all()
    assert (got.t1.to_numpy() == pd.DatetimeIndex(bt.t1).tz_convert("UTC").to_numpy()).all()
    assert np.allclose(got.exit.to_numpy(), bt.exit.to_numpy(), atol=0.011)
