"""The daily brief (spec 5).

One generated Markdown file answering spec 5's five questions in order. Static
by design: "Interactive UI is a later concern and adds no analytical value."

TWO OF THE FIVE QUESTIONS CANNOT BE ANSWERED YET, AND THE BRIEF SAYS SO
-----------------------------------------------------------------------
Section 3 ("what's expected") needs a consensus vendor, which spec 1.2 calls the
one genuinely necessary paid input. Nothing has been bought, the releases table
is empty, and the surprise indices do not exist.

Section 4's first item ("implied policy path vs. one week ago") needs a week of
stored strips, and CME publishes no settlement archive.

A brief that quietly rendered three sections and omitted two would read as
complete. Every missing section is therefore printed with what is missing, why,
and what would fix it. An honest hole is more useful than an invisible one --
that is the whole argument of this codebase, applied to its own output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .analytics import BoardParams, attribute, build_board, build_panel, classify, fit
from .config import Settings
from .errors import UnknownSeriesError, XactxError
from .logging import get_logger

# T79: was `import duckdb`, with a DuckDB connection type in every signature below.
# `store.db` is still the only module that knows the engine (its docstring always said
# so); `Connection` is the alias it exports, so the next swap is one line there.
from .store.db import Connection
from .store.query import get

log = get_logger("brief")

# Rows in the "what happened" table. Spec 5: "top 10 by |z|".
BOARD_ROWS = 10

# Per-asset residuals listed under "why".
RESIDUAL_ROWS = 6

# Above this share of a day's move being unexplained, the brief says plainly
# that there is no macro story rather than narrating the factor scores.
UNEXPLAINED_DOMINATES = 0.5

# Trailing observations a "where does this sit" percentile is measured over.
LEVEL_PERCENTILE_WINDOW = 756


@dataclass
class Section:
    """One answer, or one documented absence of an answer."""

    title: str
    body: str
    available: bool = True
    missing_reason: str = ""

    # A whole unanswered question reads differently from an unavailable item
    # inside an answered one. Section 4 can be present and useful while its
    # policy-path subsection is not, and collapsing the two would both mislead
    # the reader and make the count ambiguous.
    UNANSWERED_MARKER = "**This question cannot be answered yet.**"

    def render(self) -> str:
        if self.available:
            return f"## {self.title}\n\n{self.body}\n"
        return (
            f"## {self.title}\n\n"
            f"{self.UNANSWERED_MARKER}\n\n{self.missing_reason}\n"
        )


def _fmt(value, spec: str = ".2f", dash: str = "—") -> str:
    if value is None:
        return dash
    try:
        if isinstance(value, float) and not np.isfinite(value):
            return dash
        return format(value, spec)
    except (TypeError, ValueError):
        return str(value)


def _level_percentile(
    conn: Connection,
    series_id: str,
    as_of: datetime,
    window: int = LEVEL_PERCENTILE_WINDOW,
) -> tuple[float | None, float | None, int, date | None]:
    """Latest level and where it sits in its own trailing history.

    Returns (level, percentile, n_history, value_date). Percentile is None when
    there is too little history to place it, rather than a number computed from
    a handful of points.

    A series that is not registered at all returns the same empty result rather
    than raising. The brief names every series it wants in its tables, so an
    absent one still appears as a row with a dash: visible, but not fatal. A
    report that refuses to render because one optional series was never
    ingested is less useful than one that shows the gap.
    """
    start = as_of.date() - timedelta(days=int(window * 1.75))
    try:
        df = get(conn, series_id, start, as_of.date(), as_of=as_of, warn_empty=False)
    except UnknownSeriesError:
        log.warning("brief: %s is not registered; rendering it as absent", series_id)
        return None, None, 0, None
    if df.empty:
        return None, None, 0, None
    values = df["value"].astype(float)
    latest = float(values.iloc[-1])
    # DuckDB returns DATE columns as datetime64, so convert at the boundary
    # rather than rendering "2026-09-11 00:00:00" in a report.
    value_date = pd.Timestamp(df["value_date"].iloc[-1]).date()
    history = values.tail(window)
    if len(history) < 30:
        return latest, None, len(history), value_date
    pct = 100.0 * float((history.to_numpy() <= latest).sum()) / len(history)
    return latest, pct, len(history), value_date


# --- 1. what happened --------------------------------------------------------


def section_what_happened(
    conn: Connection, as_of: datetime, settings: Settings
) -> Section:
    params = BoardParams(
        as_of=as_of,
        zscore_window=settings.zscore_window,
        min_observations=settings.min_zscore_observations,
        max_gap_days=settings.max_gap_days,
        vol_percentile_window=settings.vol_percentile_window,
        vol_extreme_low_pct=settings.vol_extreme_low_pct,
        vol_extreme_high_pct=settings.vol_extreme_high_pct,
        stale_warn_days=settings.stale_warn_days,
    )
    board = build_board(conn, params)
    scored = board[board["status"] == "ok"]
    if scored.empty:
        return Section(
            "1. What happened", "", False,
            "No series produced a scoreable change. Check that the universe has "
            "been ingested.",
        )

    lines = [
        (f"Changes scored against their own trailing {params.zscore_window}-day "
        "distribution, which **excludes the change being scored**. Sorted by |z|."),
        "",
        "| series | date | change | unit | z | %ile | n | vol | stale |",
        "|---|---|---:|---|---:|---:|---:|---|---|",
    ]
    for r in scored.head(BOARD_ROWS).itertuples():
        vol = f"{r.vol_flag} ({r.vol_percentile:.0f})" if r.vol_flag else ""
        stale = f"{r.stale_days}d" if r.stale_days > params.stale_warn_days else ""
        lines.append(
            f"| `{r.series_id}` | {r.value_date} | {r.change:+.2f} | {r.change_unit} "
            f"| {r.z:+.2f} | {r.percentile:.0f} | {r.window_n} | {vol} | {stale} |"
        )

    excluded = board[board["status"] != "ok"]
    if not excluded.empty:
        counts = excluded["status"].value_counts()
        summary = ", ".join(f"{n} {status}" for status, n in counts.items())
        lines += ["", f"Not scored: {summary}."]

    compressed = scored[scored["vol_flag"] == "compressed"]
    if not compressed.empty:
        lines += [
            "",
            (f"**{len(compressed)} of these sit in a compressed-volatility "
            "window.** A z of 2 against a becalmed window is a different event "
            "from a z of 2 against a normal one (spec 3.1)."),
        ]
    return Section("1. What happened", "\n".join(lines))


# --- 2. why ------------------------------------------------------------------


def section_why(
    conn: Connection, as_of: datetime, settings: Settings
) -> Section:
    try:
        panel = build_panel(
            conn, as_of,
            window=settings.pca_window,
            max_gap_days=settings.max_gap_days,
            min_series=settings.pca_min_series,
        )
        model = fit(panel, settings.pca_components)
        att = attribute(panel, model)
    except XactxError as e:
        return Section("2. Why", "", False, f"Factor decomposition failed: {e}")

    lines = [
        (f"Rolling PCA over {len(panel.series)} series x {len(panel.frame)} "
        f"complete rows. Factors are labelled from their loadings, not asserted."),
        "",
        "| factor | window variance | today's move | score | loads on |",
        "|---|---:|---:|---:|---|",
    ]
    for i, ratio in enumerate(model.explained_variance_ratio):
        lines.append(
            f"| f{i + 1} | {ratio * 100:.1f}% | "
            f"{att.variance_share[i] * 100:.1f}% | {att.scores[i]:+.2f} | "
            f"{model.label(i)} |"
        )
    lines += [
        f"| **unexplained** | | **{att.unexplained_share * 100:.1f}%** | | |",
        "",
    ]

    if att.unexplained_share > UNEXPLAINED_DOMINATES:
        lines += [
            (
                f"> **The factors explain less than half of this move "
                f"({(1 - att.unexplained_share) * 100:.0f}%).** Whatever "
                f"happened on {att.value_date} is mostly idiosyncratic. There "
                f"is no macro story to tell here, and the factor scores above "
                f"should not be read as one."
            ),
            "",
        ]

    lines += [
        (f"Largest per-asset residuals for {att.value_date} (standardised units, "
        "actual minus fitted):"),
        "",
        "| series | residual | actual | fitted |",
        "|---|---:|---:|---:|",
    ]
    for sid, value in att.dominant_residuals(RESIDUAL_ROWS).items():
        lines.append(
            f"| `{sid}` | {value:+.2f} | {att.actual[sid]:+.2f} | "
            f"{att.fitted[sid]:+.2f} |"
        )

    if panel.stale_days:
        lines += [
            "",
            (f"> The panel ends {panel.frame.index[-1]}, **{panel.stale_days} days "
            f"behind the freshest data**, because alignment ends where its "
            f"slowest member ends and nothing is filled (spec 7). Capped by "
            f"`{'`, `'.join(panel.limiting_series[:4])}`."),
        ]
    for w in model.warnings:
        lines += ["", f"> Factor warning: {w}"]

    try:
        reading = classify(
            conn, as_of,
            window_days=settings.regime_window_days,
            score_window=settings.regime_score_window,
            min_z=settings.regime_min_z,
            max_gap_days=settings.max_gap_days,
        )
        evidence = ", ".join(
            f"`{k}` {v:+.2f}sd" for k, v in reading.evidence().items()
        )
        lines += [
            "",
            (f"**Regime ({reading.window_days}-day): {reading.state.upper()}** — "
            f"{reading.detail}."),
            "",
            f"Evidence: {evidence}.",
        ]
    except XactxError as e:
        lines += ["", f"> Regime classification unavailable: {e}"]

    return Section("2. Why", "\n".join(lines))


# --- 3. what's expected ------------------------------------------------------


def section_expected(conn: Connection, as_of: datetime) -> Section:
    n_releases = conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    if n_releases == 0:
        return Section(
            "3. What's expected", "", False,
            "This section needs an economic-calendar and consensus vendor, which "
            "spec 1.2 calls *\"the one genuinely necessary paid input\"* "
            "(~$30-100/mo: Trading Economics, Econoday or FMP). Nothing is "
            "subscribed, the `releases` table is empty, and the growth, "
            "inflation and labour surprise indices of spec 2.3 do not exist.\n\n"
            "When a vendor is connected this section will carry the next five "
            "scheduled releases with consensus, prior and historical surprise "
            "beta, plus the three surprise indices tracked separately — spec 2.3 "
            "is explicit that a single composite destroys the divergence between "
            "them, which is the informative part.\n\n"
            "One thing must be built alongside it: the vendor's consensus has to "
            "be **snapshotted daily** into `releases.consensus_as_of`. Spec 7 "
            "warns that some vendors overwrite consensus after the release, "
            "which would silently turn every event study into hindsight.",
        )
    return Section(
        "3. What's expected", "", False,
        f"The `releases` table holds {n_releases} rows but no surprise-index "
        "machinery exists yet (spec 2.3, phase 4).",
    )


# --- 4. what's priced --------------------------------------------------------


def section_priced(
    conn: Connection, as_of: datetime, settings: Settings
) -> Section:
    lines: list[str] = []

    # Implied policy path, and why it is empty.
    path_rows = conn.execute(
        "SELECT COUNT(DISTINCT value_date) FROM observations "
        "WHERE series_id LIKE 'policy.ff.meeting_%'"
    ).fetchone()[0]
    lines += ["### Implied policy path", ""]
    if path_rows < 2:
        lines += [
            ("**Not available.** Spec 5 asks for the path *versus one week ago*, "
            "which needs a week of stored strips. "
            f"{path_rows} trade date(s) are stored."),
            "",
            ("CME publishes no free settlement archive and prohibits automated "
            "access to its settlements endpoint, so the path cannot be "
            "backfilled — it accumulates forward from whenever settlements are "
            "first supplied. Run `xactx policy <file> --trade-date ... --store` "
            "daily to start building it."),
            "",
        ]
    else:
        lines += ["Stored trade dates: " + str(path_rows), ""]

    # Breakevens.
    lines += ["### Inflation compensation", "",
              "| series | level | %ile of own history | n | as of |",
              "|---|---:|---:|---:|---|"]
    for sid in ("be.5y", "be.10y", "be.5y5y"):
        level, pct, n, vd = _level_percentile(conn, sid, as_of)
        lines.append(
            f"| `{sid}` | {_fmt(level)}% | {_fmt(pct, '.0f')} | {n} | {vd or '—'} |"
        )
    lines += [
        "",
        ("> Breakevens carry a liquidity premium and are not clean expectations "
        "(spec 2.2)."),
        "",
    ]

    # Credit.
    lines += ["### Credit", "",
              "| series | level | %ile of own history | n | as of |",
              "|---|---:|---:|---:|---|"]
    for sid in ("credit.ig.oas", "credit.hy.oas", "credit.hy_ig.diff"):
        level, pct, n, vd = _level_percentile(conn, sid, as_of)
        lines.append(
            f"| `{sid}` | {_fmt(level)}% | {_fmt(pct, '.0f')} | {n} | {vd or '—'} |"
        )
    lines += [
        "",
        ("> **These percentiles are not three-year percentiles.** FRED's ICE "
        "licence provides only a trailing three-year window and the early end "
        "disappears as time passes, so the history above is whatever this store "
        "has accumulated. Spec 5's credit-spread percentiles are not computable "
        "from FRED alone."),
        "",
    ]

    # Vol term structure.
    lines += ["### Volatility term structure", "",
              "| series | level | %ile of own history | n | as of |",
              "|---|---:|---:|---:|---|"]
    ratios = {}
    for sid in ("vol.vix", "vol.vix9d_ratio", "vol.vix3m_ratio", "vol.skew"):
        level, pct, n, vd = _level_percentile(conn, sid, as_of)
        ratios[sid] = level
        lines.append(
            f"| `{sid}` | {_fmt(level, '.3f')} | {_fmt(pct, '.0f')} | {n} "
            f"| {vd or '—'} |"
        )
    lines.append("")
    front = ratios.get("vol.vix9d_ratio")
    term = ratios.get("vol.vix3m_ratio")
    if front is not None and term is not None:
        if front > 1.0 or term < 1.0:
            lines += [
                ("> **Backwardation.** VIX9D above spot or VIX3M below it means "
                "near-term risk is priced above longer-dated. Spec 2.2 calls "
                "this a strong regime marker."),
            ]
        else:
            lines += [
                ("> Contango: VIX9D below spot and VIX3M above it, the ordinary "
                "shape."),
            ]
    lines.append("")

    # Positioning.
    lines += ["### Positioning", "",
              "| contract | net spec | 3y %ile | as of |",
              "|---|---:|---:|---|"]
    for key in ("ust10y", "es", "dxy", "crude", "gold"):
        raw, _, _, vd = _level_percentile(conn, f"pos.cot.{key}", as_of)
        pct, _, _, _ = _level_percentile(conn, f"pos.cot.{key}.pctile", as_of)
        lines.append(
            f"| `{key}` | {_fmt(raw, ',.0f')} | {_fmt(pct, '.0f')} | {vd or '—'} |"
        )
    lines += [
        "",
        ("> Read the percentile, not the count: contract counts are not "
        "comparable across time as open interest grows (spec 2.2). Tuesday-"
        "dated, published the following Friday."),
    ]
    return Section("4. What's priced", "\n".join(lines))


# --- 5. what it affects ------------------------------------------------------


def section_affects(
    conn: Connection, as_of: datetime, settings: Settings
) -> Section:
    from . import graph

    try:
        graph.register(conn)
        stats, skipped = graph.estimate_all(
            conn, as_of,
            beta_window=settings.beta_window,
            corr_history=settings.corr_history_window,
            max_gap_days=settings.max_gap_days,
        )
    except XactxError as e:
        return Section("5. What it affects", "", False, f"Graph unavailable: {e}")

    active = [s for s in stats if s.significant]
    conflicts = [s for s in stats if s.sign_conflict]
    extremes = [s for s in stats if s.corr_extreme]

    lines = [
        (f"{len(stats)} edges estimated over a {settings.beta_window}-day window, "
        f"{len(active)} with a beta distinguishable from zero."),
        "",
        "| edge | prior | lag | beta | t | r² | corr | %ile |",
        "|---|:-:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in sorted(active, key=lambda e: -abs(e.beta_t_stat)):
        d = s.definition
        prior = {1: "+", -1: "−", 0: "?"}[d.expected_sign]
        lines.append(
            f"| `{d.from_series}` → `{d.to_series}` | {prior} | "
            f"{d.typical_lag_days} | {s.beta:+.3f} | {s.beta_t_stat:+.1f} | "
            f"{s.r_squared:.2f} | {s.corr:+.2f} | {s.corr_percentile:.0f} |"
        )
    lines += [
        "",
        ("> A prior of `?` is deliberate: the sign is regime-dependent and "
        "asserting one would mislead precisely when it matters (spec 4). Read "
        "the percentile on those edges, not the sign."),
        "",
    ]

    if conflicts:
        lines += ["### Sign conflicts", "",
                  "The beta disagrees with the prior **and** is significant.", ""]
        for c in conflicts:
            d = c.definition
            lines += [
                (f"- **`{d.from_series}` → `{d.to_series}`**: prior "
                f"{d.expected_sign:+d}, beta {c.beta:+.3f} (t {c.beta_t_stat:+.1f}), "
                f"correlation {c.corr:+.2f} at the {c.corr_percentile:.0f}th "
                f"percentile of {c.corr_history_n} readings."),
                f"  {d.note}",
            ]
        lines.append("")

    if extremes:
        lines += ["### Correlations at an extreme of their own history", ""]
        for e in extremes:
            d = e.definition
            lines.append(
                f"- `{d.from_series}` → `{d.to_series}`: {e.corr:+.2f}, "
                f"**{e.corr_percentile:.0f}th percentile** of {e.corr_history_n} "
                f"readings ({e.corr_extreme})."
            )
        lines.append("")

    if skipped:
        lines += [
            f"### {len(skipped)} edges defined but not computable", "",
            ("Kept in the graph rather than removed: a graph containing only the "
            "convenient edges hides its own shape."), "",
        ]
        for (a, b) in sorted(skipped):
            lines.append(f"- `{a}` → `{b}`")
    return Section("5. What it affects", "\n".join(lines))


# --- assembly ----------------------------------------------------------------


def generate(
    conn: Connection, as_of: datetime, settings: Settings
) -> str:
    """The whole brief, as Markdown."""
    return generate_with_count(conn, as_of, settings)[0]


def generate_with_count(
    conn: Connection, as_of: datetime, settings: Settings
) -> tuple[str, int]:
    """The brief, plus how many of the five questions it could not answer."""
    sections = [
        section_what_happened(conn, as_of, settings),
        section_why(conn, as_of, settings),
        section_expected(conn, as_of),
        section_priced(conn, as_of, settings),
        section_affects(conn, as_of, settings),
    ]

    missing = [s for s in sections if not s.available]
    total_obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    n_series = conn.execute(
        "SELECT COUNT(*) FROM series_metadata"
    ).fetchone()[0]

    head = [
        f"# Cross-asset brief — {as_of.date()}",
        "",
        (f"Generated {as_of.isoformat()} from {total_obs:,} observations across "
        f"{n_series} series."),
        "",
        ("Every figure below was read with an explicit `as_of`, so this is the "
        "brief that could have been produced at that moment and nothing in it "
        "had been published later."),
        "",
    ]
    if missing:
        head += [
            f"> **{len(missing)} of the five questions cannot be answered yet:** "
            + ", ".join(f"*{s.title}*" for s in missing)
            + ". Each says why in place rather than being omitted.",
            "",
        ]
    head += ["---", ""]

    body = "\n".join(head) + "\n\n".join(s.render() for s in sections)

    provenance = conn.execute(
        "SELECT as_of_basis, COUNT(*) FROM observations GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    body += (
        "\n---\n\n## Provenance\n\n"
        "How the `as_of` on each stored observation was established:\n\n"
        "| basis | rows |\n|---|---:|\n"
        + "".join(f"| `{b}` | {n:,} |\n" for b, n in provenance)
        + "\n`source_vintage` is the publisher's own vintage date. `derived_lag` "
        "is the value date plus a documented publication convention. "
        "`archive_floor` means the value was known *by* that time but the true "
        "publication date is earlier and unrecoverable.\n"
    )
    return body, len(missing)


def write(
    conn: Connection,
    as_of: datetime,
    settings: Settings,
    directory,
) -> tuple[object, str, int]:
    """Write the brief and report how many of the five questions went
    unanswered, so a caller cannot arrive at a different count from the one the
    document itself states."""
    from pathlib import Path

    text, unanswered = generate_with_count(conn, as_of, settings)
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"brief-{as_of.date():%Y-%m-%d}.md"
    path.write_text(text, encoding="utf-8")
    log.info(
        "brief: wrote %s (%d characters, %d question(s) unanswered)",
        path, len(text), unanswered,
    )
    return path, text, unanswered
