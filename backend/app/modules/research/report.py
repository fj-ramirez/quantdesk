"""Leaderboard report: a static HTML file each research cycle regenerates.

Includes the noise ceiling — the top OOS Sharpe that pure luck would produce
given how many trials the registry has accumulated. Candidates must clear it
decisively before they deserve paper trading.
"""

from __future__ import annotations

import html
import statistics
from datetime import datetime, timezone

from .backtest import noise_ceiling
from .config import REPORTS_DIR
from .registry import Registry

_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>EdgeLab Leaderboard</title>
<style>
:root {{ --accent: #2f6fd6; --muted: #9aa1ab; --border: #ddd; }}
body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 1250px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
th, td {{ padding: 5px 9px; border-bottom: 1px solid var(--border); text-align: right; }}
th {{ background: #f5f5f5; }} td.l, th.l {{ text-align: left; }}
.good {{ color: #0a7d33; font-weight: 600; }}
.warn {{ background: #fff6e0; border: 1px solid #e6c765; padding: 10px 14px;
        border-radius: 6px; margin: 1rem 0; }}
.health {{ color: #555; font-size: 0.9rem; }}
.stale {{ font-weight: 700; }}
.spark polyline {{ fill: none; stroke: var(--accent); stroke-width: 1.5; }}
.spark line {{ stroke: var(--muted); stroke-width: 1; stroke-dasharray: 2 2; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --accent: #7aa5f0; --muted: #6b7280; --border: #374151; }}
  body {{ background: #14181f; color: #e5e7eb; }}
  th {{ background: #1f242e; }}
  .good {{ color: #4ade80; }}
  .warn {{ background: #33290f; border-color: #7a6420; }}
  .health {{ color: #a7adb7; }}
}}
</style></head><body>
<h1>EdgeLab Leaderboard</h1>
<p>Generated {now} &middot; total trials ever run: <b>{total}</b></p>
{health}
<div class="warn"><b>Noise ceiling:</b> with {total} trials, pure noise would
produce a best OOS Sharpe around <b>{ceiling:.2f}</b> (at the median OOS span
of {median_years:.1f} years; each row is highlighted against a ceiling for its
own span). Rows are only interesting if OOS Sharpe clears the ceiling
decisively <i>and</i> survives paper trading. OOS data is reused across
cycles, so treat the leaderboard as a shortlist generator, not proof.</div>
{portfolio}
{paper}
{families}
<h2>Backtest leaderboard (shortlist only)</h2>
{table}
</body></html>"""

_PAPER_HEAD = """<h2>Paper trading — forward performance</h2>
<p>Computed only on bars that arrived <i>after</i> each candidate was
promoted. This data has never been touched by any selection step, so it is
the trustworthy number. Returns are volatility-targeted as deployable money
would trade them (see config <code>sizing</code>). Give candidates several
weeks before judging. <a href="candidates.html">Full documentation per
candidate</a>.</p>"""

_PAPER_COLS = [
    ("market", "Market", "l"), ("strategy", "Strategy", "l"), ("symbol", "Symbol", "l"),
    ("timeframe", "TF", "l"), ("params", "Params", "l"), ("spark", "OOS equity", "l"),
    ("promoted", "Promoted", "l"),
    ("days", "Days", ""), ("fwd_bars", "Fwd bars", ""), ("fwd_sharpe", "Fwd Sharpe", ""),
    ("fwd_return", "Fwd ret", ""), ("fwd_max_dd", "Fwd MaxDD", ""),
    ("oos_sharpe_then", "OOS then", ""), ("sharpe_2x", "2x-cost", ""),
    ("neighbor_med", "Nbr med", ""), ("wf", "WF+", ""), ("wf_med", "WF med", ""),
    ("corr_max", "Corr@prom", ""),
]

_COLS = [
    ("market", "Market", "l"), ("strategy", "Strategy", "l"), ("symbol", "Symbol", "l"),
    ("timeframe", "TF", "l"), ("params", "Params", "l"),
    ("is_sharpe", "IS Sharpe", ""), ("oos_sharpe", "OOS Sharpe", ""),
    ("oos_cagr", "OOS CAGR", ""), ("oos_max_dd", "OOS MaxDD", ""),
    ("oos_fills", "Fills", ""), ("oos_years", "OOS Yrs", ""), ("run_date", "Tested", "l"),
]


def _fmt(key: str, v) -> str:
    if v is None:
        return "&ndash;"
    if key in ("oos_cagr", "oos_max_dd", "fwd_return", "fwd_max_dd"):
        return f"{v:.1%}"
    if isinstance(v, float):
        return f"{v:.2f}"
    return html.escape(str(v))


def _sparkline(values: list[float] | None) -> str:
    """Tiny inline-SVG equity curve; dashed baseline at 1.0x when in range."""
    if not values or len(values) < 2:
        return ""
    w, h, pad = 120, 26, 2
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1.0
    n = len(values)
    pts = " ".join(
        f"{pad + i * (w - 2 * pad) / (n - 1):.1f},"
        f"{h - pad - (v - lo) / rng * (h - 2 * pad):.1f}"
        for i, v in enumerate(values)
    )
    base = ""
    if lo <= 1.0 <= hi:
        yb = h - pad - (1.0 - lo) / rng * (h - 2 * pad)
        base = f'<line x1="{pad}" y1="{yb:.1f}" x2="{w - pad}" y2="{yb:.1f}"/>'
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img"><title>OOS equity ended at {values[-1]:.2f}x</title>'
            f'{base}<polyline points="{pts}"/></svg>')


STALE_HOURS = 48


def _health_line(cfg: dict, registry: Registry) -> str:
    from datetime import date

    from .data import cache_ages

    parts = []
    for market, (n, oldest) in cache_ages(cfg).items():
        if n == 0 or oldest is None:
            parts.append(f'{market}: <span class="stale">NO DATA</span>')
        elif oldest > STALE_HOURS:
            parts.append(f'{market}: {n} cached, <span class="stale">'
                         f'STALE {oldest:.0f}h</span>')
        else:
            parts.append(f"{market}: {n} cached, {oldest:.0f}h old")
    today = registry.trials_on(date.today().isoformat())
    return ('<p class="health">Health &mdash; ' + " &middot; ".join(parts)
            + f" &middot; trials today: {today}</p>")


FAMILY_MIN_TESTED = 5
FAMILY_TOP_N = 15


def _family_table(registry: Registry) -> str:
    """Aggregate trials by strategy x instrument. One lucky parameter set is
    noise; a whole family whose *median* variant makes money out-of-sample is
    a much stronger signal than any single row."""
    from collections import defaultdict

    groups: dict[tuple, list[float]] = defaultdict(list)
    for market, strategy, symbol, tf, oos in registry.family_rows():
        groups[(market, strategy, symbol, tf)].append(oos)

    stats = []
    for key, sharpes in groups.items():
        if len(sharpes) < FAMILY_MIN_TESTED:
            continue
        stats.append((statistics.median(sharpes), max(sharpes),
                      sum(1 for s in sharpes if s > 0) / len(sharpes), len(sharpes), key))
    stats.sort(reverse=True)
    stats = stats[:FAMILY_TOP_N]
    if not stats:
        return ""

    head = ("<h2>Strongest families (all variants aggregated)</h2>"
            "<p>Ranked by <i>median</i> OOS Sharpe across every parameter variant "
            "ever tested — clusters, not lucky rows.</p>"
            '<table><tr><th class="l">Market</th><th class="l">Strategy</th>'
            '<th class="l">Symbol</th><th class="l">TF</th><th>Variants</th>'
            "<th>Median OOS</th><th>Best OOS</th><th>% positive</th></tr>")
    body = []
    for med, best, pct, n, (market, strategy, symbol, tf) in stats:
        body.append(f'<tr><td class="l">{html.escape(market)}</td>'
                    f'<td class="l">{html.escape(strategy)}</td>'
                    f'<td class="l">{html.escape(symbol)}</td>'
                    f'<td class="l">{tf}</td><td>{n}</td><td>{med:.2f}</td>'
                    f"<td>{best:.2f}</td><td>{pct:.0%}</td></tr>")
    return head + "".join(body) + "</table>"


def _portfolio_box(p: dict | None) -> str:
    if p is None:
        return ""
    return (f"<h2>Watchlist as a portfolio</h2>"
            f"<p>Equal-weight across <b>{p['n']}</b> candidates over their OOS"
            f" segments ({p['days']} trading days): combined Sharpe"
            f" <b>{p['sharpe']:.2f}</b>, average pairwise correlation"
            f" <b>{p['avg_corr']:.2f}</b>, highest <b>{p['max_corr']:.2f}</b>"
            f" ({html.escape(p['max_pair'])}). Low correlations are the point —"
            f" several modest uncorrelated edges beat one heroic one.</p>")


def _paper_table(paper_rows: list[dict]) -> str:
    if not paper_rows:
        return _PAPER_HEAD + "<p>No candidates promoted yet.</p>"
    body = ["<table><tr>"
            + "".join(f'<th class="{c}">{t}</th>' for _, t, c in _PAPER_COLS) + "</tr>"]
    for r in paper_rows:
        cells = []
        for k, _, c in _PAPER_COLS:
            v = _sparkline(r.get("spark")) if k == "spark" else _fmt(k, r[k])
            cells.append(f'<td class="{c}">{v}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    body.append("</table>")
    return _PAPER_HEAD + "".join(body)


_CAND_CAVEATS = {
    "futures": ("Yahoo continuous futures carry roll gaps — verify on "
                "back-adjusted data before funding."),
    "xs": "Universe is today's constituents: results carry survivorship bias.",
    "all": ("Backtest numbers reuse the same OOS segment across cycles; only "
            "forward (paper) performance is selection-free evidence."),
}


def write_candidates(cfg: dict, paper_rows: list[dict]) -> str:
    """One documentation block per watchlist candidate: the rule in plain
    English, the regime and its detection, gate values, forward stats, caveats.
    This page is the reproducible statement of what is being traded and why."""
    import json

    from . import xs
    from .strategies import FAMILIES, REGIME_DOCS

    blocks = []
    for r in paper_rows:
        params = json.loads(r["params"])
        if xs.is_xs(r["symbol"]):
            rule = xs.describe(r["market"], params)
        elif r["strategy"] in FAMILIES:
            rule = FAMILIES[r["strategy"]].describe(params)
        else:
            rule = f"{r['strategy']} with {params}"
        regime = params.get("regime", "any")

        caveats = [_CAND_CAVEATS["all"]]
        if r["market"] == "futures":
            caveats.insert(0, _CAND_CAVEATS["futures"])
        if xs.is_xs(r["symbol"]):
            caveats.insert(0, _CAND_CAVEATS["xs"])

        blocks.append(
            f"<h2>{html.escape(r['symbol'])} &middot; {html.escape(r['strategy'])}"
            f" &middot; {r['timeframe']} ({html.escape(r['market'])})</h2>"
            f"<p><b>Rule:</b> {html.escape(rule)}</p>"
            f"<p><b>Regime:</b> {html.escape(regime)} &mdash; "
            f"{html.escape(REGIME_DOCS.get(regime, ''))}</p>"
            f"<p><b>Exact parameters:</b> <code>{html.escape(r['params'])}</code></p>"
            f"<p><b>Promoted:</b> {r['promoted']} &middot; OOS Sharpe then "
            f"{_fmt('x', r['oos_sharpe_then'])} &middot; at 2x costs "
            f"{_fmt('x', r['sharpe_2x'])} &middot; parameter-neighbor median "
            f"{_fmt('x', r['neighbor_med'])} &middot; walk-forward {r['wf']} windows "
            f"positive &middot; watchlist correlation at promotion "
            f"{_fmt('x', r['corr_max'])}</p>"
            # Through `_fmt`, which renders an unmeasured value as a dash. These are None
            # until a candidate has two forward bars, and a raw `:.2f` would raise on it.
            f"<p><b>Forward so far:</b> {r['days']} days, {r['fwd_bars']} bars, "
            f"Sharpe {_fmt('x', r['fwd_sharpe'])}, return "
            f"{_fmt('fwd_return', r['fwd_return'])}, "
            f"max drawdown {_fmt('fwd_max_dd', r['fwd_max_dd'])}</p>"
            f"<p>{_sparkline(r.get('spark'))}</p>"
            "<p><b>Caveats:</b> " + " ".join(html.escape(c) for c in caveats) + "</p><hr>"
        )

    page = _PAGE.format(
        now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        total="", ceiling=0, median_years=0, health="", families="",
        portfolio="", paper="", table="")
    # Reuse only the styled shell; replace body content wholesale.
    head, _, _ = page.partition("<h1>")
    body = ("<h1>EdgeLab Candidate Documentation</h1>"
            "<p>Generated automatically each cycle. Each block is the complete, "
            "reproducible statement of one paper-trading candidate.</p>"
            + "".join(blocks) + "</body></html>")
    out = REPORTS_DIR / "candidates.html"
    out.write_text(head + body, encoding="utf-8")
    return str(out)


def write_report(cfg: dict, registry: Registry, paper_rows: list[dict] | None = None,
                 portfolio: dict | None = None) -> dict:
    rows = registry.leaderboard(
        cfg["filters"]["min_trades_oos"], cfg["filters"]["min_exposure"], cfg["report"]["top_n"]
    )
    total = registry.total_trials()

    median_years = statistics.median([r["oos_years"] for r in rows]) if rows else 1.0
    ceiling = noise_ceiling(total, median_years)

    n_above = 0
    body = ["<table><tr>" + "".join(f'<th class="{c}">{t}</th>' for _, t, c in _COLS) + "</tr>"]
    for r in rows:
        row_ceiling = noise_ceiling(total, r["oos_years"])
        above = r["oos_sharpe"] > row_ceiling
        n_above += above
        cells = []
        for key, _, c in _COLS:
            cls = c + (" good" if key == "oos_sharpe" and above else "")
            cells.append(f'<td class="{cls}">{_fmt(key, r[key])}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    body.append("</table>")
    if not rows:
        body = ["<p>No trials pass the leaderboard filters yet.</p>"]

    out = REPORTS_DIR / "leaderboard.html"
    out.write_text(
        _PAGE.format(now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                     total=total, ceiling=ceiling, median_years=median_years,
                     health=_health_line(cfg, registry),
                     families=_family_table(registry),
                     portfolio=_portfolio_box(portfolio),
                     paper=_paper_table(paper_rows or []),
                     table="".join(body)),
        encoding="utf-8",
    )
    return {"report": str(out), "total_trials": total, "noise_ceiling": round(ceiling, 2),
            "candidates_above_ceiling": n_above}
