"""Ledger #438: entry replicator for the eurusd-burst-fade provider (spec frozen in
docs/edges/eurusd-burst-fade.md, *Entry replicator*). Exit fixed: TP 5 / SL 25 / 24 h.

    uv run --directory backend python ../scripts/edges/2026-10-12-burst-replicator.py data/broker-export [--tp 5] [--holdout]
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIP, PT = 1e-4, 1e-5
NY = "America/New_York"
D = Path(sys.argv[1])
HOLDOUT = "--holdout" in sys.argv
TP = float(sys.argv[sys.argv.index("--tp") + 1]) if "--tp" in sys.argv else 5.0  # pips; #438 = 5, #439 = 10
m1 = pd.read_parquet(D / "eurusd.parquet").sort_values("ts").set_index("ts")
m1.index = m1.index.tz_convert(None)
m1 = m1[~m1.index.duplicated()]
if not HOLDOUT:
    # development runs never load the holdout period (2023-01-01 -> 2026-04-26, 2026-07-11 -> 10-05)
    keep = (m1.index < "2023-01-01") | ((m1.index >= "2026-03-01") & (m1.index < "2026-07-12"))
    m1 = m1[keep]
T = m1.index.to_numpy().astype("datetime64[ns]")
O, H, L, C, SP = (m1[k].to_numpy(float) for k in ("open", "high", "low", "close", "spread"))
SPX = SP * PT


def bars(rule):
    if rule == "1D":
        ny = m1.index.tz_localize("UTC").tz_convert(NY)
        key = (ny - pd.Timedelta(hours=17)).normalize().tz_localize(None)
        b = m1.groupby(key).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                                last=("close", lambda s: s.index[-1]))
        b.index = b["last"] + pd.Timedelta(minutes=1)
        return b.drop(columns="last")
    b = m1.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    b.index = b.index + pd.Timedelta(rule)
    return b


def atr(b, n=14):
    pc = b["close"].shift(1)
    return pd.concat([b["high"] - b["low"], (b["high"] - pc).abs(), (b["low"] - pc).abs()], axis=1).max(axis=1).ewm(alpha=1 / n).mean()


def rsi(c, n=14):
    d = c.diff()
    return 100 - 100 / (1 + d.clip(lower=0).ewm(alpha=1 / n).mean() / (-d.clip(upper=0)).ewm(alpha=1 / n).mean())


B5, B15, BH, BD = bars("5min"), bars("15min"), bars("1h"), bars("1D")
ev = B5.index  # decisions at M5 closes
px = B5["close"].to_numpy()


def at(s):  # value of a completed-bar series known at each M5 close
    return s.reindex(ev.union(s.index)).ffill().reindex(ev).to_numpy()


F = {}
for nm, b in (("m5", B5), ("m15", B15), ("h1", BH)):
    c = b["close"]
    mid, sd = at(c.rolling(20).mean()), at(c.rolling(20).std())
    F[f"{nm}_pctb"] = (px - (mid - 2 * sd)) / (4 * sd)
    F[f"{nm}_rsi"] = at(rsi(c))
ll, hh = B5["low"].rolling(14).min(), B5["high"].rolling(14).max()
F["m5_stoch"] = (100 * (B5["close"] - ll) / (hh - ll)).rolling(3).mean().to_numpy()
cl = pd.Series(px, index=ev)
for n in (15, 60, 240, 1440):
    F[f"ret{n}"] = (px - cl.reindex(ev - pd.Timedelta(minutes=n), method="ffill").to_numpy()) / PIP
F["m15_ema50_atr"] = (px - at(B15["close"].ewm(span=50).mean())) / at(atr(B15))
e20, e50, e200 = (at(BD["close"].ewm(span=n).mean()) for n in (20, 50, 200))
F["d1_stack"] = np.sign(e20 - e50) + np.sign(e50 - e200)
F["d1_ema50_atr"] = (px - e50) / at(atr(BD))
hi_age = 19 - B15["high"].rolling(20).apply(np.argmax, raw=True)
lo_age = 19 - B15["low"].rolling(20).apply(np.argmin, raw=True)
F["m15_bars_since_hi"] = at(hi_age)
FL = pd.DataFrame(F, index=ev)
# mirrored features for buys
M = FL.copy()
for k in ("m5_pctb", "m15_pctb", "h1_pctb"):
    M[k] = 1 - FL[k]
for k in ("m5_rsi", "m15_rsi", "h1_rsi", "m5_stoch"):
    M[k] = 100 - FL[k]
for k in ("ret15", "ret60", "ret240", "ret1440", "m15_ema50_atr", "d1_stack", "d1_ema50_atr"):
    M[k] = -FL[k]
M["m15_bars_since_hi"] = at(lo_age)
COLS = list(FL.columns)

hours = ev.hour
universe = (hours >= 7) & (hours < 21)

# ---- training on the burst weeks
obs = pd.read_csv(D / "burst-entries.csv", parse_dates=["ot"])
sell_t = (obs.loc[obs.side == "sell", "ot"] - pd.Timedelta(hours=3)).to_numpy().astype("datetime64[ns]")
evn = ev.to_numpy().astype("datetime64[ns]")
label = np.zeros(len(ev), bool)
pos = np.searchsorted(evn, sell_t, side="right") - 1  # the M5 close just before the entry
label[pos[pos >= 0]] = True
train = universe & (ev >= "2026-04-27") & (ev < "2026-07-11") & FL.notna().all(axis=1).to_numpy()
Xtr, ytr = FL[train].to_numpy(float), label[train].astype(float)
mu, sd = Xtr.mean(0), Xtr.std(0)
Z = (Xtr - mu) / sd
w = np.zeros(Z.shape[1])
b0 = math.log(ytr.mean() / (1 - ytr.mean()))
lam = 1.0
for _ in range(50):  # IRLS (Newton) on the L2-penalised log-likelihood
    p = 1 / (1 + np.exp(-(Z @ w + b0)))
    g_w = Z.T @ (p - ytr) + lam * w
    g_b = (p - ytr).sum()
    Wd = p * (1 - p)
    Hm = np.zeros((len(w) + 1, len(w) + 1))
    Hm[:-1, :-1] = Z.T @ (Z * Wd[:, None]) + lam * np.eye(len(w))
    Hm[:-1, -1] = Hm[-1, :-1] = Z.T @ Wd
    Hm[-1, -1] = Wd.sum()
    step = np.linalg.solve(Hm, np.concatenate([g_w, [g_b]]))
    w -= step[:-1]
    b0 -= step[-1]
print(f"trained on {int(ytr.sum())} provider sells vs {len(ytr)} M5 closes (07-21 UTC, burst weeks)")
print("weights (standardised):", ", ".join(f"{c} {v:+.2f}" for c, v in sorted(zip(COLS, w), key=lambda x: -abs(x[1]))))


def score(frame):
    return ((frame[COLS].to_numpy(float) - mu) / sd) @ w + b0


S_sell, S_buy = score(FL), score(M)
s_tr = S_sell[train]
auc_in = (pd.Series(s_tr).rank()[ytr == 1].sum() - ytr.sum() * (ytr.sum() + 1) / 2) / (ytr.sum() * (len(ytr) - ytr.sum()))
print(f"in-sample AUC {auc_in:.3f}")


def episodes_per_day(mask_idx):
    t = evn[mask_idx]
    if len(t) == 0:
        return 0.0
    epi = 1 + int((np.diff(t) > np.timedelta64(30, "m")).sum())
    return epi / 52


tr_idx = np.flatnonzero(train)
thr = None
for q in np.linspace(0.90, 0.9995, 400):
    t_ = np.quantile(s_tr, q)
    if episodes_per_day(tr_idx[s_tr >= t_]) <= 1.3:
        thr = t_
        break
print(f"threshold {thr:.3f} (≈1.3 sell episodes/day in the burst weeks)")
hit = tr_idx[s_tr >= thr]
recall = np.mean([((evn[hit] >= s - np.timedelta64(30, "m")) & (evn[hit] <= s + np.timedelta64(5, "m"))).any() for s in sell_t])
print(f"in-sample recall of provider sells (signal within -30/+5 min): {recall:.2f}")


def simulate(wins):
    ok = np.zeros(len(ev), bool)
    for a, b_ in wins:
        ok |= (ev >= a) & (ev < b_)
    ok &= universe
    side = np.where(ok & (S_sell >= thr), -1, np.where(ok & (S_buy >= thr), 1, 0))
    rows, busy = [], np.datetime64("1970-01-01", "ns")
    for i in np.flatnonzero(side):
        if evn[i] < busy:
            continue
        k = np.searchsorted(T, evn[i])
        if k >= len(T) or (T[k] - evn[i]) > np.timedelta64(10, "m") or not (0 < SP[k] <= 15):
            continue
        sd_ = int(side[i])
        j_end = np.searchsorted(T, T[k] + np.timedelta64(24, "h"))
        if j_end >= len(T):
            break
        seg = slice(k, j_end)
        if sd_ == -1:
            e = O[k]
            sl_hit = H[seg] + SPX[seg] >= e + 25 * PIP
            tp_hit = L[seg] + SPX[seg] <= e - TP * PIP
        else:
            e = O[k] + SPX[k]
            sl_hit = L[seg] <= e - 25 * PIP
            tp_hit = H[seg] >= e + TP * PIP
        fs = int(np.argmax(sl_hit)) if sl_hit.any() else 10**9
        ft = int(np.argmax(tp_hit)) if tp_hit.any() else 10**9
        if fs <= ft and fs < 10**9:
            j = k + fs
            opn = O[j] + SPX[j] if sd_ == -1 else O[j]
            g = -max(25 * PIP, (opn - e) if sd_ == -1 else (e - opn)) / PIP
            kind = "SL"
        elif ft < 10**9:
            j = k + ft
            g, kind = TP, "TP"
        else:
            j = j_end
            px_ = O[j] + SPX[j] if sd_ == -1 else O[j]
            g, kind = ((e - px_) if sd_ == -1 else (px_ - e)) / PIP, "time"
        rows.append((pd.Timestamp(T[k]), sd_, g, SPX[k] / PIP, kind))
        busy = T[j]
    tr = pd.DataFrame(rows, columns=["t0", "side", "gross", "spread", "kind"])
    tr["net"] = tr["gross"] - tr["spread"]
    return tr


def report(name, tr, gate=False):
    if tr.empty:
        print(f"\n== {name}: no trades")
        return
    x = tr["net"]
    day = tr["t0"].dt.tz_localize("UTC").dt.tz_convert(NY).dt.date.to_numpy()
    dev = (x - x.mean()).groupby(day).sum()
    t = x.mean() / (math.sqrt((dev ** 2).sum()) / len(x)) if len(x) > 2 else float("nan")
    yrs = tr.groupby(tr["t0"].dt.year)["net"].sum()
    months = max(tr["t0"].dt.to_period("M").nunique(), 1)
    print(f"\n== {name}: n={len(x)} mean {x.mean():+.2f} pips (gross {tr['gross'].mean():+.2f}) t {t:+.2f} "
          f"win {(x > 0).mean():.1%} trades/month {len(x) / months:.1f} $/month@0.01 {x.sum() * 0.1 / months:+.2f}")
    print(f"   exits {tr['kind'].value_counts().to_dict()} | sells {tr.loc[tr.side == -1, 'net'].mean():+.2f} "
          f"(n={(tr.side == -1).sum()}) buys {tr.loc[tr.side == 1, 'net'].mean():+.2f} (n={(tr.side == 1).sum()})")
    print("   years:", " ".join(f"{y}:{v:+.0f}" for y, v in yrs.items()), f"| positive {(yrs > 0).sum()}/{len(yrs)}")
    if gate:
        ok = len(x) >= 100 and x.mean() > 0 and t >= 2 and (yrs > 0).sum() >= 3
        print(f"   gate (n>=100, mean>0, t>=2, >=3 years +) -> {'PASS' if ok else 'fail'}")


report("burst weeks (in-sample, reference)", simulate([("2026-04-27", "2026-07-11")]))
if HOLDOUT:
    report("HOLDOUT 2023-01 -> 2026-04-26 + 2026-07-11 -> 10-05", simulate([("2023-01-01", "2026-04-27"), ("2026-07-11", "2026-10-06")]), gate=True)
else:
    report("DEVELOPMENT 2019-2022", simulate([("2019-01-01", "2023-01-01")]), gate=True)
