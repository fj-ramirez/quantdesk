"""Backlog 53: the 4h desk strategy (the user's rule, declared 2026-10-16). Ledger 548-549.

4h candles from M1, anchored at 17:00 New York. Long while flat at a 4h close with close > EMA20,
EMA20 > EMA50 and MACD line (EMA8 - EMA17) > 0, filled at the next M1 open. Initial stop one
spread below the signal candle's low; a recovered pullback lifts it to the pullback low minus one
spread. Exit on the stop (M1 low) or on a 4h close < EMA20 (next M1 open). Scored as a position
rule on daily P&L marked at 16:00 NY, with per-trade R beside it. Family 4h-ema-macd-trend, k = 2.

Backlog 54 (ledger 550-553) reruns it with a wider initial stop: argv[2] = atr2 (entry - 2 x ATR(14)
of 4h, Wilder) or swing5 (lowest low of the last 5 candles - one spread). Default candle = row 53.
Family k = 6 after row 54.
"""
import math
import sys

import numpy as np
import pandas as pd
from _common import INDICES, NY, SPLIT, XAU_SWAP, _rollovers, at, load, rolls, sessions

data = sys.argv[1]
STOP = sys.argv[2] if len(sys.argv) > 2 else "candle"
#: Backlog 55 (ledger 554-559): take-profit at entry + TP x initial risk; argv[3], none by default.
TP = float(sys.argv[3]) if len(sys.argv) > 3 else None
WARMUP = 200


def bars4h(df: pd.DataFrame) -> pd.DataFrame:
    wall = df.index.tz_localize(None)
    b = (wall - pd.Timedelta(hours=17)).floor("4h") + pd.Timedelta(hours=17)
    pos = np.arange(len(df))
    g = pd.DataFrame({"b": b, "pos": pos, "open": df["open"].to_numpy(), "high": df["high"].to_numpy(),
                      "low": df["low"].to_numpy(), "close": df["close"].to_numpy()}).groupby("b", sort=True)
    out = pd.DataFrame({"first": g["pos"].min(), "last": g["pos"].max(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last()})
    c = out["close"]
    out["ema20"] = c.ewm(span=20, adjust=False).mean()
    out["ema50"] = c.ewm(span=50, adjust=False).mean()
    out["macd"] = c.ewm(span=8, adjust=False).mean() - c.ewm(span=17, adjust=False).mean()
    tr = pd.concat([out["high"] - out["low"], (out["high"] - c.shift()).abs(), (out["low"] - c.shift()).abs()],
                   axis=1).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return out.reset_index(drop=True)


def simulate(sym: str) -> pd.DataFrame:
    df = load(data, sym)
    o, lo, sp = df["open"].to_numpy(), df["low"].to_numpy(), df["spread"].to_numpy()
    hi = df["high"].to_numpy()
    ts = df.index
    B = bars4h(df)
    hi4, lo4, c4, e20 = B["high"].to_numpy(), B["low"].to_numpy(), B["close"].to_numpy(), B["ema20"].to_numpy()
    sig = (c4 > e20) & (e20 > B["ema50"].to_numpy()) & (B["macd"].to_numpy() > 0)
    first, last = B["first"].to_numpy(), B["last"].to_numpy()
    n_m1, out = len(df), []
    i = WARMUP
    while i < len(B) - 1:
        if not sig[i]:
            i += 1
            continue
        e = last[i] + 1
        if e >= n_m1:
            break
        entry, s = o[e], sp[e]
        if STOP == "atr2":
            stop = entry - 2 * B["atr14"].iat[i]
        elif STOP == "swing5":
            stop = lo4[i - 4:i + 1].min() - s
        else:
            stop = lo4[i] - s
        init_stop = stop
        target = entry + TP * (entry - init_stop) if TP else None
        pb_hh = pb_ll = None
        exit_pos = exit_px = None
        reason = None
        if entry <= stop:  # gapped below the stop at the fill
            exit_pos, exit_px, reason = e, entry, "gap"
            j = i + 1
        j = i + 1
        while exit_pos is None and j < len(B):
            a = max(first[j], e)
            seg = lo[a:last[j] + 1]
            hit = np.nonzero(seg <= stop)[0]
            win = np.nonzero(hi[a:last[j] + 1] >= target)[0] if target is not None else []
            if len(hit) and (not len(win) or hit[0] <= win[0]):  # both in one M1 bar: the stop first
                m = a + hit[0]
                exit_pos, exit_px, reason = m, min(o[m], stop) if m > e else stop, "stop"
                break
            if len(win):
                m = a + win[0]
                exit_pos, exit_px, reason = m, max(o[m], target) if m > e else target, "target"
                break
            if c4[j] < e20[j]:
                if last[j] + 1 < n_m1:
                    exit_pos, exit_px, reason = last[j] + 1, o[last[j] + 1], "ema20"
                break
            # trail: pullback bookkeeping at this close
            if pb_hh is not None and c4[j] > pb_hh:
                stop = max(stop, pb_ll - s)
                pb_hh = pb_ll = None
            elif c4[j] < c4[j - 1]:
                pb_hh = hi4[j] if pb_hh is None else max(pb_hh, hi4[j])
                pb_ll = lo4[j] if pb_ll is None else min(pb_ll, lo4[j])
            elif pb_hh is not None:
                pb_hh, pb_ll = max(pb_hh, hi4[j]), min(pb_ll, lo4[j])
            j += 1
        if exit_pos is None:
            break  # still open at the end of the data
        out.append({"t0": ts[e], "t1": ts[exit_pos], "entry": entry, "exit": exit_px, "spread": s,
                    "init_stop": init_stop, "reason": reason})
        # next signal can only come from a close at or after the exit bar
        i = max(j, i + 1)
    tr = pd.DataFrame(out)
    tr["roll"] = 0.0
    if sym.endswith(".fs"):
        r = rolls(data)
        for _, row in r[r.cfd_symbol == sym].iterrows():
            t = row.rolled_at.tz_convert(NY)
            hit = (tr.t0 <= t) & (tr.t1 > t)
            tr.loc[hit, "roll"] += row.step * tr.loc[hit, "entry"]
    tr["swap"] = 0.0
    if sym == "XAUUSD":
        tr["swap"] = _rollovers(pd.DatetimeIndex(tr.t0), pd.DatetimeIndex(tr.t1)) * XAU_SWAP[1] * 0.01
    tr["gross"] = tr.exit - tr.entry - tr.roll
    tr["net"] = tr.gross - 2 * tr.spread + tr.swap
    tr["risk"] = tr.entry - tr.init_stop
    # a gap fill at or below the stop has no initial risk, so no R (still in the daily P&L at full cost)
    tr["R"] = (tr.net / tr.risk).where(tr.risk > 0)
    tr["pct"] = tr.net / tr.entry * 100
    return tr


def daily(sym: str, tr: pd.DataFrame):
    """Mark-to-market % P&L per session (16:00 NY), and buy-and-hold on the same closes."""
    df = load(data, sym)
    d = sessions(data, sym)
    t16 = d + pd.Timedelta(hours=16)
    c16, _, ok = at(df, t16)
    d, t16, c16 = d[ok], t16[ok], c16[ok]
    pnl = np.zeros(len(d))
    expo = np.zeros(len(d), dtype=bool)
    rl = rolls(data)
    rl = rl[rl.cfd_symbol == sym]
    roll_t = pd.DatetimeIndex(rl.rolled_at).tz_convert(NY) if len(rl) else pd.DatetimeIndex([], tz=NY)
    for t in tr.itertuples():
        k0 = t16.searchsorted(t.t0)
        k1 = t16.searchsorted(t.t1)
        marks_t = [t.t0] + list(t16[k0:k1]) + [t.t1]
        marks_p = [t.entry] + list(c16[k0:k1]) + [t.exit]
        for n in range(len(marks_t) - 1):
            k = min(k0 + n, len(d) - 1)
            mv = marks_p[n + 1] - marks_p[n]
            for rt, st in zip(roll_t, rl.step):
                if marks_t[n] <= rt < marks_t[n + 1]:
                    mv -= st * marks_p[n]
            if n == 0:
                mv -= 2 * t.spread
            if sym == "XAUUSD":
                mv += _rollovers(pd.DatetimeIndex([marks_t[n]]), pd.DatetimeIndex([marks_t[n + 1]]))[0] \
                    * XAU_SWAP[1] * 0.01
            pnl[k] += mv / t.entry * 100
            expo[k] = True
    strat = pd.Series(pnl, index=d)
    mv = np.diff(c16)
    for rt, st in zip(roll_t, rl.step):
        h = (t16[:-1] <= rt) & (t16[1:] > rt)
        mv[h] -= st * c16[:-1][h]
    bh = mv / c16[:-1] * 100
    if sym == "XAUUSD":
        bh += _rollovers(t16[:-1], t16[1:]) * XAU_SWAP[1] * 0.01 / c16[:-1] * 100
    bh = pd.Series(bh, index=d[1:])
    span = strat.index >= tr.t0.min().normalize()
    return strat[span], expo[span], bh[bh.index >= tr.t0.min().normalize()]


def tstat(v):
    v = np.asarray(v)
    return float(v.mean() / v.std(ddof=1) * math.sqrt(len(v))) if len(v) > 2 else float("nan")


def sharpe(v):
    return float(np.mean(v) / np.std(v, ddof=1) * math.sqrt(252))


def clustered_se(x, groups):
    x = np.asarray(x)
    dev = pd.Series(x - x.mean()).groupby(np.asarray(groups)).sum().to_numpy()
    n = len(x)
    return math.sqrt((dev ** 2).sum()) / n


results = {}
for sym in INDICES + ["XAUUSD"]:
    tr = simulate(sym)
    tr.to_csv(f"{data}/4h-desk-trend-{STOP}{f'-tp{TP:g}' if TP else ''}-{sym.replace('&', '-').lower()}.csv", index=False)
    strat, expo, bh = daily(sym, tr)
    x = strat[expo]
    oos = x.index >= SPLIT.normalize()
    yrs = strat.groupby(strat.index.year).sum()
    rr = tr.dropna(subset=["R"])
    se = clustered_se(rr.R, pd.DatetimeIndex(rr.t0).normalize())
    hold_days = (pd.DatetimeIndex(tr.t1) - pd.DatetimeIndex(tr.t0)).total_seconds() / 86400
    res = dict(days=int(expo.sum()), entries=len(tr), mean=x.mean(), t=tstat(x), is_mean=x[~oos].mean(),
               oos_mean=x[oos].mean(), is_t=tstat(x[~oos]), oos_t=tstat(x[oos]), sharpe=sharpe(strat),
               bh_sharpe=sharpe(bh), total=strat.sum(), bh_total=bh.sum(), years_pos=int((yrs > 0).sum()),
               years_n=len(yrs))
    ok = (res["days"] >= 100 and res["entries"] >= 10 and res["is_mean"] > 0 and res["oos_mean"] > 0
          and res["years_pos"] > res["years_n"] / 2 and res["sharpe"] > res["bh_sharpe"])
    res["verdict"] = "pass" if ok and res["t"] >= 2 else "lead" if ok else "fail"
    results[sym] = res
    print(f"\n== {sym}  {tr.t0.min():%Y-%m-%d} -> {tr.t1.max():%Y-%m-%d}")
    print(f"  trades {len(tr)}, win {np.mean(tr.net > 0):.0%}, hold median {np.median(hold_days):.1f} d, "
          f"exits {tr.reason.value_counts().to_dict()}")
    print(f"  per trade: mean R {tr.R.mean():+.3f} +/- {se:.3f} (clustered) t={tr.R.mean() / se:+.2f} | "
          f"median R {tr.R.median():+.2f} | mean {tr.pct.mean():+.3f}% (gross {(tr.gross / tr.entry).mean() * 100:+.3f}%,"
          f" cost {(2 * tr.spread / tr.entry).mean() * 100:.3f}%, swap {(tr.swap / tr.entry).mean() * 100:+.3f}%)")
    print(f"  cost in R: {(2 * tr.spread / tr.risk).mean():.3f} | median initial risk {(tr.risk / tr.entry).median() * 100:.2f}%")
    print(f"  daily: {res['days']} in-market days, mean {res['mean']:+.4f}% t={res['t']:+.2f} | IS {res['is_mean']:+.4f}% "
          f"t={res['is_t']:+.2f} OOS {res['oos_mean']:+.4f}% t={res['oos_t']:+.2f} | years+ {res['years_pos']}/{res['years_n']}")
    print(f"  Sharpe {res['sharpe']:.2f} vs B&H {res['bh_sharpe']:.2f} | total {res['total']:+.1f}% vs B&H {res['bh_total']:+.1f}%"
          f" | in market {expo.mean():.0%} of sessions -> {res['verdict']}")
    print("  years:", " ".join(f"{y}:{v:+.1f}" for y, v in yrs.items()),
          "| B&H:", " ".join(f"{y}:{v:+.1f}" for y, v in bh.groupby(bh.index.year).sum().items()))

a = [results[s]["verdict"] == "pass" for s in INDICES]
print(f"\ncell A (all three indices): {'pass' if all(a) else 'fail'}; cell B (XAUUSD): {results['XAUUSD']['verdict']}")
