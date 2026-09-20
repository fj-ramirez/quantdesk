"""Paper-trading harness: forward-tracks promoted candidates.

Promotion freezes a candidate (strategy + params + instrument) with a
timestamp. Forward stats are then computed only on bars that arrived *after*
promotion — data that no selection step has ever touched. Unlike the
leaderboard's reused OOS segment, this is immune to multiple-testing bias,
which makes it the platform's real gatekeeper: only candidates that survive
weeks of forward tracking deserve real money.

Candidates are never auto-retired; a public record of failures is part of the
honesty (and retiring losers would bias the survivors' aggregate stats).
"""

from __future__ import annotations

import json
import logging
import math

import pandas as pd

from .backtest import noise_ceiling, run_backtest, vol_targeted
from .data import load_ohlcv
from .registry import Registry

log = logging.getLogger(__name__)


def _sized(cfg: dict, close: pd.Series, pos: pd.Series) -> pd.Series:
    """Apply the deployable-money sizing overlay if enabled."""
    scfg = cfg.get("sizing", {})
    if not scfg.get("enabled", False):
        return pos
    return vol_targeted(close, pos, scfg["vol_target_annual"],
                        scfg["vol_lookback"], scfg["max_leverage"])


def _oos_daily(cfg: dict, cand: dict, cache: dict) -> pd.Series | None:
    """Daily-resampled OOS net returns for a candidate dict (params as JSON str)."""
    from . import xs
    from .strategies import FAMILIES

    mcfg = cfg["markets"].get(cand["market"])
    if mcfg is None:
        return None
    key = (cand["market"], cand["symbol"], cand["timeframe"])

    if xs.is_xs(cand["symbol"]):
        if key not in cache:
            cache[key] = xs.load_panel(cand["market"], mcfg, cand["timeframe"])
        panel = cache[key]
        if panel is None:
            return None
        net = xs.oos_net(panel, json.loads(cand["params"]), cfg["split"]["is_fraction"],
                         mcfg["costs"]["fee_bps"], mcfg["costs"]["slippage_bps"])
        return net.resample("1D").sum()

    strat = FAMILIES.get(cand["strategy"])
    if strat is None:
        return None
    if key not in cache:
        cache[key] = load_ohlcv(cand["market"], mcfg, cand["symbol"], cand["timeframe"])
    df = cache[key]
    if df is None or len(df) == 0:
        return None
    pos = _sized(cfg, df["close"], strat.positions(df, json.loads(cand["params"])))
    split = int(len(df) * cfg["split"]["is_fraction"])
    _, net = run_backtest(df["close"].iloc[split:], pos.iloc[split:],
                          mcfg["costs"]["fee_bps"], mcfg["costs"]["slippage_bps"])
    return net.resample("1D").sum()


MIN_CORR_OVERLAP_DAYS = 60


def _max_corr(s: pd.Series, members: list[pd.Series]) -> float:
    """Highest correlation of s against member series on overlapping days."""
    worst = 0.0
    for ms in members:
        joined = pd.concat([s, ms], axis=1, join="inner").dropna()
        if len(joined) < MIN_CORR_OVERLAP_DAYS:
            continue
        c = joined.corr().iloc[0, 1]
        if pd.notna(c):
            worst = max(worst, float(c))
    return worst


def promote(cfg: dict, registry: Registry) -> int:
    """Promote leaderboard rows clearing a fraction of their noise ceiling
    AND passing the robustness gates (doubled costs, parameter neighborhood)."""
    from .registry import trial_hash
    from .robustness import check
    from .strategies import FAMILIES
    from .validation import walkforward

    pcfg, rcfg, wcfg = cfg["paper"], cfg["robustness"], cfg["walkforward"]
    rows = registry.leaderboard(
        cfg["filters"]["min_trades_oos"], cfg["filters"]["min_exposure"],
        cfg["report"]["top_n"],
    )
    total = registry.total_trials()
    now = pd.Timestamp.now(tz="UTC").isoformat()

    room = pcfg["max_candidates"] - registry.paper_count()
    budget = min(pcfg["promote_per_cycle"], room)
    added = 0
    cache: dict[tuple, pd.DataFrame | None] = {}
    # One watchlist slot per hypothesis: parameter variants of the same
    # strategy/instrument/regime are redundant evidence, not new ideas.
    members = registry.paper_all()
    taken = {
        (c["market"], c["strategy"], c["symbol"], c["timeframe"],
         json.loads(c["params"]).get("regime", "any"))
        for c in members
    }
    member_returns = [s for c in members if (s := _oos_daily(cfg, c, cache)) is not None]
    for r in rows:
        if added >= budget:
            break
        if r["oos_sharpe"] < pcfg["min_fraction_of_ceiling"] * noise_ceiling(total, r["oos_years"]):
            continue
        params = json.loads(r["params"])
        group = (r["market"], r["strategy"], r["symbol"], r["timeframe"],
                 params.get("regime", "any"))
        if group in taken:
            continue
        h = trial_hash(r["market"], r["strategy"], r["symbol"], r["timeframe"], params)
        if registry.paper_has(h):
            continue

        from . import xs

        mcfg = cfg["markets"].get(r["market"])
        if mcfg is None:
            continue
        fee, slip = mcfg["costs"]["fee_bps"], mcfg["costs"]["slippage_bps"]
        key = (r["market"], r["symbol"], r["timeframe"])

        if xs.is_xs(r["symbol"]):
            if key not in cache:
                cache[key] = xs.load_panel(r["market"], mcfg, r["timeframe"])
            if cache[key] is None:
                continue
            sharpe_2x, neighbor_med, wf_pos, wf_active, wf_med = xs.gates(
                cache[key], params, cfg["split"]["is_fraction"], fee, slip,
                rcfg["cost_multiplier"], wcfg["n_windows"])
        else:
            strat = FAMILIES.get(r["strategy"])
            if strat is None:
                continue
            if key not in cache:
                cache[key] = load_ohlcv(r["market"], mcfg, r["symbol"], r["timeframe"])
            df = cache[key]
            if df is None:
                continue
            sharpe_2x, neighbor_med = check(
                df, strat, params, cfg["split"]["is_fraction"], fee, slip,
                rcfg["cost_multiplier"])
            wf_pos, wf_active, wf_med = walkforward(
                df, strat, params, fee, slip, wcfg["n_windows"])

        if sharpe_2x <= rcfg["min_sharpe_2x"] or neighbor_med <= rcfg["min_neighbor_median"]:
            log.info("robustness rejected: %s %s %s %s (2x-cost %.2f, neighbor med %.2f)",
                     r["market"], r["strategy"], r["symbol"], r["timeframe"],
                     sharpe_2x, neighbor_med)
            continue
        if (wf_active < wcfg["min_active_windows"]
                or wf_pos < math.ceil(wcfg["min_positive_frac"] * wf_active)):
            log.info("walk-forward rejected: %s %s %s %s (positive in %d of %d active windows)",
                     r["market"], r["strategy"], r["symbol"], r["timeframe"],
                     wf_pos, wf_active)
            continue

        cand_returns = _oos_daily(cfg, r | {"params": r["params"]}, cache)
        corr_max = _max_corr(cand_returns, member_returns) if cand_returns is not None else 0.0
        if corr_max > pcfg["max_corr"]:
            log.info("correlation rejected: %s %s %s %s (%.2f vs watchlist, limit %.2f)",
                     r["market"], r["strategy"], r["symbol"], r["timeframe"],
                     corr_max, pcfg["max_corr"])
            continue

        registry.paper_add(h, now, r["market"], r["strategy"], r["symbol"],
                           r["timeframe"], r["params"], r["oos_sharpe"],
                           sharpe_2x, neighbor_med, wf_pos, wf_active, wf_med, corr_max)
        taken.add(group)
        if cand_returns is not None:
            member_returns.append(cand_returns)
        added += 1
        log.info("promoted to paper: %s %s %s %s (OOS %.2f, 2x-cost %.2f, "
                 "neighbor med %.2f, WF %d/%d, corr %.2f)",
                 r["market"], r["strategy"], r["symbol"], r["timeframe"],
                 r["oos_sharpe"], sharpe_2x, neighbor_med, wf_pos, wf_active, corr_max)
    registry.commit()
    return added


def forward_stats(cfg: dict, registry: Registry, persist: bool = True) -> list[dict]:
    """Compute forward performance for every paper candidate, and record it (T83).

    The computation is unchanged and was always right: signals are generated over the full
    history so indicators are warm, then performance is sliced to bars strictly *after*
    `promoted_at`, starting flat. That slice is the only evidence in EdgeLab no selection step
    has ever touched.

    What changed is that the answer is now stored. It used to go into `report.html` and
    nowhere else, so the database -- and therefore the API and the MCP connector -- held
    promotion-time gates and no forward record at all. `persist` writes one `paper_scores` row
    per candidate per run, append-only, which turns a single number into a trajectory.

    Pass `persist=False` for a read-only view; `nightly.run_cycle` uses the default. The
    caller commits, as everywhere else in this module.
    """
    from .strategies import FAMILIES

    out = []
    now = pd.Timestamp.now(tz="UTC")
    cache: dict[tuple, pd.DataFrame | None] = {}
    for c in registry.paper_all():
        mcfg = cfg["markets"].get(c["market"])
        row = {
            "hash": c["hash"],
            "market": c["market"], "strategy": c["strategy"], "symbol": c["symbol"],
            "timeframe": c["timeframe"], "params": c["params"],
            "promoted": c["promoted_at"][:10],
            "days": (now - pd.Timestamp(c["promoted_at"])).days,
            "oos_sharpe_then": c["promoted_oos_sharpe"],
            "sharpe_2x": c["sharpe_2x"], "neighbor_med": c["neighbor_med"],
            "wf": f"{c['wf_pos']}/{c['wf_active']}",
            "wf_med": c["wf_med"], "corr_max": c["corr_max"],
            # `fwd_bars` is a real count, so 0 is honest: zero bars have been traded. The three
            # floats are `None` -- *unknown* -- until there are at least two forward bars to
            # compute them from. They were 0.0 here, which rendered as a flat forward record
            # and read as evidence of nothing happening rather than of nothing measured yet.
            # Same rule invariant 3 states for open interest.
            "fwd_bars": 0, "fwd_sharpe": None, "fwd_return": None, "fwd_max_dd": None,
            "spark": None,
        }
        oos_ret = _oos_daily(cfg, c, cache)
        if oos_ret is not None and len(oos_ret) >= 2:
            eq = (1.0 + oos_ret).cumprod()
            step = max(1, len(eq) // 100)
            row["spark"] = [round(float(v), 4) for v in eq.iloc[::step]]
        from . import xs

        if mcfg is None or (not xs.is_xs(c["symbol"]) and c["strategy"] not in FAMILIES):
            out.append(row)
            continue
        fee, slip = mcfg["costs"]["fee_bps"], mcfg["costs"]["slippage_bps"]
        cut = pd.Timestamp(c["promoted_at"])
        key = (c["market"], c["symbol"], c["timeframe"])

        if xs.is_xs(c["symbol"]):
            if key not in cache:
                cache[key] = xs.load_panel(c["market"], mcfg, c["timeframe"])
            panel = cache[key]
            if panel is None:
                out.append(row)
                continue
            w = xs.weights(panel, json.loads(c["params"]))
            fwd = panel.index > cut
            if fwd.sum() >= 2:
                m, net = xs.xs_backtest(panel[fwd], w[fwd], fee, slip)
                row.update(fwd_bars=m.n_bars, fwd_sharpe=m.sharpe,
                           fwd_return=float((1 + net).prod() - 1), fwd_max_dd=m.max_drawdown)
            out.append(row)
            continue

        if key not in cache:
            cache[key] = load_ohlcv(c["market"], mcfg, c["symbol"], c["timeframe"])
        df = cache[key]
        if df is None or len(df) == 0:
            out.append(row)
            continue

        # Signals need full history for indicator warmup; performance is then
        # sliced to bars strictly after promotion (starting flat).
        pos = _sized(cfg, df["close"],
                     FAMILIES[c["strategy"]].positions(df, json.loads(c["params"])))
        fwd = df.index > cut
        if fwd.sum() >= 2:
            m, net = run_backtest(df["close"][fwd], pos[fwd], fee, slip)
            row.update(fwd_bars=m.n_bars, fwd_sharpe=m.sharpe,
                       fwd_return=float((1 + net).prod() - 1), fwd_max_dd=m.max_drawdown)
        out.append(row)

    if persist:
        # One `scored_at` for the whole run, not one per candidate: these measurements share a
        # cycle, and a common timestamp is what lets "the watchlist as of this run" be a single
        # query instead of a window over near-identical instants.
        scored_at = pd.Timestamp.now(tz="UTC")
        for row in out:
            registry.paper_score_add(
                row["hash"], scored_at, row["days"], row["fwd_bars"],
                row["fwd_sharpe"], row["fwd_return"], row["fwd_max_dd"],
            )

    # Unscored candidates sort last rather than mixing in among the negatives: a candidate with
    # no forward Sharpe yet has not underperformed, it has not been measured.
    out.sort(key=lambda r: (r["fwd_sharpe"] is not None, r["fwd_sharpe"] or 0.0), reverse=True)
    return out


def portfolio_stats(cfg: dict, registry: Registry) -> dict | None:
    """Equal-weight portfolio view of the watchlist over each candidate's OOS
    segment: combined Sharpe and pairwise correlations of daily returns.

    Modest edges become an income when they are *uncorrelated* — this shows
    whether the watchlist is accumulating diversification or five copies of
    the same bet. Rough by design (calendar alignment across 24/7 and
    session markets); read it directionally.
    """
    cands = registry.paper_all()
    if len(cands) < 2:
        return None

    series: dict[str, pd.Series] = {}
    cache: dict[tuple, pd.DataFrame | None] = {}
    for c in cands:
        s = _oos_daily(cfg, c, cache)
        if s is not None:
            series[f"{c['symbol']} {c['strategy']} {c['timeframe']}"] = s

    if len(series) < 2:
        return None
    aligned = pd.DataFrame(series).fillna(0.0)
    aligned = aligned[(aligned != 0).any(axis=1)]
    if len(aligned) < 30:
        return None

    port = aligned.mean(axis=1)
    std = float(port.std())
    sharpe = float(port.mean()) / std * math.sqrt(252) if std > 0 else 0.0

    corr = aligned.corr()
    pairs = [
        (corr.iloc[i, j], corr.index[i], corr.columns[j])
        for i in range(len(corr)) for j in range(i + 1, len(corr))
    ]
    avg_corr = sum(p[0] for p in pairs) / len(pairs)
    max_corr, a, b = max(pairs, key=lambda p: p[0])
    return {
        "n": len(series), "sharpe": sharpe, "avg_corr": avg_corr,
        "max_corr": max_corr, "max_pair": f"{a} vs {b}", "days": len(aligned),
    }
