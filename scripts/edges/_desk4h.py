"""The 4h desk strategy's simulator, shared by backlog rows 56+ (2026-10-16).

Row 53's rule (see `2026-10-16-53-4h-desk-trend.py`, which stays as the record for rows 53-55)
generalised to: an entry mask (the signal, or random draws for the control), an exit mode,
and MAE/MFE per trade. With exit="pullback" and the signal mask it reproduces row 53 exactly.

Exit modes, all keeping the stop, the 4h close < EMA20 exit and stops that only rise:
  pullback  row 53: a recovered pullback lifts the stop to its low - 1 spread
  be1       pullback, plus breakeven: once an M1 high reaches entry + 1R the stop moves to
            entry + 2 spreads, from the next M1 bar (both in one bar: the stop first)
  atr3      chandelier instead of the pullback trail: highest 4h high since entry - 3 ATR(14)
  be1atr3   both
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from _common import NY, SPLIT, XAU_SWAP, _rollovers, at, load, rolls, sessions

WARMUP = 200
USD_PER_POINT = {"S&P.fs": 0.50, "NAS100.fs": 0.20, "DJ30.fs": 0.05, "XAUUSD": 1.0}
_bars: dict = {}


def bars4h(data, sym: str) -> pd.DataFrame:
    key = (str(data), sym)
    if key in _bars:
        return _bars[key]
    df = load(data, sym)
    wall = df.index.tz_localize(None)
    b = (wall - pd.Timedelta(hours=17)).floor("4h") + pd.Timedelta(hours=17)
    g = pd.DataFrame({"b": b, "pos": np.arange(len(df)), "high": df["high"].to_numpy(),
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
    _bars[key] = out.reset_index(drop=True)
    return _bars[key]


def signal_mask(data, sym: str) -> np.ndarray:
    B = bars4h(data, sym)
    return ((B["close"] > B["ema20"]) & (B["ema20"] > B["ema50"]) & (B["macd"] > 0)).to_numpy()


def simulate(data, sym: str, mask: np.ndarray, exit: str = "pullback", excursions: bool = False) -> pd.DataFrame:
    df = load(data, sym)
    o, hi, lo, sp = (df[k].to_numpy() for k in ("open", "high", "low", "spread"))
    ts = df.index
    B = bars4h(data, sym)
    hi4, lo4, c4, e20 = (B[k].to_numpy() for k in ("high", "low", "close", "ema20"))
    atr = B["atr14"].to_numpy()
    first, last = B["first"].to_numpy(), B["last"].to_numpy()
    use_pb, use_be, use_atr = exit in ("pullback", "be1"), exit in ("be1", "be1atr3"), exit in ("atr3", "be1atr3")
    n_m1, out = len(df), []
    i = WARMUP
    while i < len(B) - 1:
        if not mask[i]:
            i += 1
            continue
        e = last[i] + 1
        if e >= n_m1:
            break
        entry, s = o[e], sp[e]
        stop = init_stop = lo4[i] - s
        trigger = entry + (entry - init_stop) if use_be else None
        pb_hh = pb_ll = None
        hh = -np.inf
        exit_pos = exit_px = reason = None
        if entry <= stop:  # gapped below the stop at the fill
            exit_pos, exit_px, reason = e, entry, "gap"
        j = i + 1
        while exit_pos is None and j < len(B):
            a, b = max(first[j], e), last[j] + 1
            while a < b:  # intrabar: the stop, and the breakeven trigger, in M1 order
                hit = np.nonzero(lo[a:b] <= stop)[0]
                k = np.nonzero(hi[a:b] >= trigger)[0] if trigger is not None else []
                if len(hit) and (not len(k) or hit[0] <= k[0]):
                    m = a + hit[0]
                    exit_pos, exit_px, reason = m, min(o[m], stop) if m > e else stop, "stop"
                    break
                if len(k):
                    stop = max(stop, entry + 2 * s)
                    trigger = None
                    a = a + k[0] + 1
                    continue
                break
            if exit_pos is not None:
                break
            if c4[j] < e20[j]:
                if last[j] + 1 < n_m1:
                    exit_pos, exit_px, reason = last[j] + 1, o[last[j] + 1], "ema20"
                break
            if use_pb:
                if pb_hh is not None and c4[j] > pb_hh:
                    stop = max(stop, pb_ll - s)
                    pb_hh = pb_ll = None
                elif c4[j] < c4[j - 1]:
                    pb_hh = hi4[j] if pb_hh is None else max(pb_hh, hi4[j])
                    pb_ll = lo4[j] if pb_ll is None else min(pb_ll, lo4[j])
                elif pb_hh is not None:
                    pb_hh, pb_ll = max(pb_hh, hi4[j]), min(pb_ll, lo4[j])
            if use_atr:
                hh = max(hh, hi4[j])
                stop = max(stop, hh - 3 * atr[j])
            j += 1
        if exit_pos is None:
            break  # still open at the end of the data
        out.append({"t0": ts[e], "t1": ts[exit_pos], "e": e, "x": exit_pos, "entry": entry, "exit": exit_px,
                    "spread": s, "init_stop": init_stop, "reason": reason})
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
    tr["R"] = (tr.net / tr.risk).where(tr.risk > 0)  # a gap fill has no initial risk
    tr["pct"] = tr.net / tr.entry * 100
    if excursions:
        _excursions(data, sym, tr, hi, lo, ts)
    return tr.drop(columns=["e", "x"])


def _excursions(data, sym, tr, hi, lo, ts):
    """MAE/MFE in points: M1 bars from the entry bar up to (not incl.) the exit bar, plus the exit
    price; roll steps removed after a roll inside the trade."""
    rl = rolls(data)
    rl = rl[rl.cfd_symbol == sym]
    rt = pd.DatetimeIndex(rl.rolled_at).tz_convert(NY) if len(rl) else pd.DatetimeIndex([], tz=NY)
    mae, mfe, mae_t, mfe_t = [], [], [], []
    for t in tr.itertuples():
        h, l, tt = hi[t.e:t.x].astype(float), lo[t.e:t.x].astype(float), ts[t.e:t.x]
        adj = np.zeros(len(tt))
        for when, step in zip(rt, rl.step):
            if t.t0 <= when < t.t1:
                adj[tt >= when] += step * t.entry
        ex = t.exit - t.roll
        lows, highs = np.r_[l - adj, ex], np.r_[h - adj, ex]
        a, b = lows.argmin(), highs.argmax()
        tl = list(tt) + [t.t1]
        mae.append(max(0.0, t.entry - lows[a]))
        mfe.append(max(0.0, highs[b] - t.entry))
        mae_t.append(tl[a])
        mfe_t.append(tl[b])
    tr["mae"], tr["mfe"] = mae, mfe
    tr["mae_ts"], tr["mfe_ts"] = pd.DatetimeIndex(mae_t), pd.DatetimeIndex(mfe_t)


def daily(data, sym: str, tr: pd.DataFrame):
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
        k0, k1 = t16.searchsorted(t.t0), t16.searchsorted(t.t1)
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
    start = tr.t0.min().normalize()
    span = strat.index >= start
    return strat[span], expo[span], bh[bh.index >= start]


def tstat(v):
    v = np.asarray(v)
    return float(v.mean() / v.std(ddof=1) * math.sqrt(len(v))) if len(v) > 2 else float("nan")


def sharpe(v):
    return float(np.mean(v) / np.std(v, ddof=1) * math.sqrt(252))


def clustered_se(x, groups):
    x = np.asarray(x)
    dev = pd.Series(x - x.mean()).groupby(np.asarray(groups)).sum().to_numpy()
    return math.sqrt((dev ** 2).sum()) / len(x)


def score(data, sym: str, tr: pd.DataFrame, bar: float) -> dict:
    strat, expo, bh = daily(data, sym, tr)
    x = strat[expo]
    oos = x.index >= SPLIT.normalize()
    yrs = strat.groupby(strat.index.year).sum()
    rr = tr.dropna(subset=["R"])
    se = clustered_se(rr.R, pd.DatetimeIndex(rr.t0).normalize())
    res = dict(trades=len(tr), win=float((tr.net > 0).mean()), mean_R=float(rr.R.mean()), se_R=se,
               mean_pct=float(tr.pct.mean()), days=int(expo.sum()), t=tstat(x), is_mean=float(x[~oos].mean()),
               oos_mean=float(x[oos].mean()), sharpe=sharpe(strat), bh_sharpe=sharpe(bh),
               total=float(strat.sum()), bh_total=float(bh.sum()), years_pos=int((yrs > 0).sum()),
               years_n=len(yrs), usd=float(tr.net.sum() * USD_PER_POINT[sym]),
               exits=tr.reason.value_counts().to_dict(), years=yrs.round(1).to_dict())
    ok = (res["days"] >= 100 and res["trades"] >= 10 and res["is_mean"] > 0 and res["oos_mean"] > 0
          and res["years_pos"] > res["years_n"] / 2 and res["sharpe"] > res["bh_sharpe"])
    res["gates"] = "pass" if ok and res["t"] >= bar else "lead" if ok else "fail"
    return res
