"""Command line entry point.

    python -m xactx verify            -- check every source code is still live
    python -m xactx ingest            -- register the universe and backfill
    python -m xactx status            -- what is stored, per series
    python -m xactx derive            -- compute the derived series
    python -m xactx board             -- normalized change board (spec 3.1)
    python -m xactx fomc              -- FOMC meeting calendar
    python -m xactx policy            -- implied policy path (spec 2.1)
    python -m xactx factors           -- factor decomposition (spec 3.2)
    python -m xactx regime            -- regime classification (spec 3.4)
    python -m xactx edges             -- transmission graph (spec 4)
    python -m xactx brief             -- the daily brief (spec 5)

argparse rather than a CLI framework: eleven subcommands do not justify a
dependency.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import universe
from .adapters import CboeAdapter, CftcAdapter, FredAdapter, TreasuryAdapter
from .config import Settings, load_settings
from .errors import EmptyFetchError, UnknownSeriesError, XactxError
from .logging import configure, get_logger
from .store import Loader, Store

log = get_logger("cli")


def build_adapter(source: str, settings: Settings):
    smap = universe.series_map(source)
    kw = {"timeout": settings.http_timeout_seconds, "snapshot_tz": settings.snapshot_tz}
    if source == "fred":
        return FredAdapter(smap, settings.fred_api_key, **kw)
    if source == "treasury":
        return TreasuryAdapter(smap, **kw)
    if source == "cboe":
        return CboeAdapter(smap, **kw)
    if source == "cftc":
        return CftcAdapter(smap, **kw)
    raise UnknownSeriesError(f"no network adapter for source {source!r}")


def cmd_verify(args: argparse.Namespace, settings: Settings) -> int:
    """Check every FRED code resolves. Run this before a backfill: a dead code
    should stop the run, not silently shorten the universe (spec 7)."""
    from .adapters.fred import check_codes

    universe.check_duplicates()
    codes = [s.source_code for s in universe.by_source("fred")]
    results = check_codes(settings.fred_api_key, codes, settings.http_timeout_seconds)

    failures = {c: r for c, r in results.items() if not r.startswith("ok")}
    for code, result in sorted(results.items()):
        print(f"  {'FAIL' if code in failures else 'ok  '}  {code:16s} {result}")

    if universe.RETIRED_CODES:
        print("\nknown-retired codes (documented, never fetched):")
        for code, why in sorted(universe.RETIRED_CODES.items()):
            print(f"  {code:20s} {why}")

    print(f"\n{len(results) - len(failures)}/{len(results)} FRED codes live")
    return 1 if failures else 0


def cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    universe.check_duplicates()
    start = args.since or settings.backfill_start
    # 'today' in the snapshot timezone, not the machine's: a run after midnight
    # local but before the ET date rolls would otherwise request a value_date
    # the market has not reached.
    end = args.until or datetime.now(ZoneInfo(settings.snapshot_tz)).date()

    sources = (
        sorted(universe.FETCHABLE_SOURCES) if args.source == "all" else [args.source]
    )

    with Store(settings.db_path) as store:
        loader = Loader(store)

        # Register the whole universe, including series no adapter fills yet. A
        # universe that lists only what happened to be fetchable hides its gaps.
        for meta in universe.UNIVERSE:
            loader.register_series(meta)
        log.info("registered %d series (%d fetchable)",
                 len(universe.UNIVERSE), len(universe.fetchable()))

        if args.register_only:
            # Metadata is descriptive and safe to correct in place; observations
            # are not. This exists so a transform or note can be fixed without
            # refetching twenty years of history.
            print(f"registered {len(universe.UNIVERSE)} series; no data fetched")
            return 0

        failures: list[tuple[str, str]] = []
        filled = 0

        for source in sources:
            metas = universe.by_source(source)
            adapter = build_adapter(source, settings)
            batch = loader.start_batch(source, f"{start}..{end}")
            try:
                if source == "treasury":
                    # One document per year serves every tenor, so fetch once.
                    obs = adapter.fetch([m.source_code for m in metas], start, end, batch)
                    loader.persist(obs)
                    filled += len(metas)
                else:
                    for meta in metas:
                        try:
                            extra = (
                                {"frequency": meta.frequency, "revisable": meta.revisable}
                                if source == "fred"
                                else {}
                            )
                            obs = adapter.fetch(
                                [meta.source_code], start, end, batch, **extra
                            )
                            loader.persist(obs)
                            filled += 1
                        except (EmptyFetchError, UnknownSeriesError) as e:
                            # Recorded and re-reported at the end; the run still
                            # exits non-zero. Never swallowed.
                            log.error("%s: %s", meta.series_id, e)
                            failures.append((meta.series_id, str(e)))
                loader.finish_batch(batch, "failed" if failures else "ok")
            except Exception as e:  # batch must be marked failed before re-raising
                loader.finish_batch(batch, "failed", str(e)[:500])
                raise
            finally:
                adapter.close()

        pending = [
            s for s in universe.UNIVERSE if s.source not in universe.FETCHABLE_SOURCES
        ]
        print(f"\nfilled       {filled}")
        print(f"no_adapter   {len(pending)}  (registered, awaiting a later phase)")
        print(f"fetch_failed {len(failures)}")
        for sid, err in failures:
            print(f"  FAIL {sid}: {err[:140]}")

    return 1 if failures else 0


def cmd_derive(args: argparse.Namespace, settings: Settings) -> int:
    """Compute and store the derived series (spec 1.3, 2.2).

    Safe to re-run. If an input has been revised since the last run, the new
    max(input as_of) makes a new vintage rather than overwriting the old one.
    """
    from . import derive

    tz = ZoneInfo(settings.snapshot_tz)
    as_of = args.as_of or datetime.now(tz)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=tz)
    start = args.since or settings.backfill_start
    end = args.until or as_of.date()

    targets = (
        [derive.get_derivation(args.target)] if args.target else list(derive.DERIVATIONS)
    )

    failures: list[tuple[str, str]] = []
    written = 0
    with Store(settings.db_path) as store:
        loader = Loader(store)
        batch = loader.start_batch("derived", f"{start}..{end} as_of={as_of.isoformat()}")
        try:
            for d in targets:
                try:
                    derive.check_units(store.conn, d)
                    obs = derive.compute(store.conn, d, start, end, as_of, batch)
                    result = loader.persist(obs)
                    written += result.inserted
                    print(f"  {d.target:22s} {result.inserted:6d} new, "
                          f"{result.duplicates:6d} already stored")
                except XactxError as e:
                    log.error("%s: %s", d.target, e)
                    failures.append((d.target, str(e)))
            loader.finish_batch(batch, "failed" if failures else "ok")
        except Exception as e:
            loader.finish_batch(batch, "failed", str(e)[:500])
            raise

    print(f"\n{written} observations written across {len(targets)} derivation(s)")
    for target, err in failures:
        print(f"  FAIL {target}: {err[:160]}")
    return 1 if failures else 0


def cmd_fomc(args: argparse.Namespace, settings: Settings) -> int:
    """Fetch or show the FOMC meeting calendar (spec 2.1).

    The calendar is the input most likely to be wrong, so it is fetched from the
    Federal Reserve and cached with the moment it was fetched, never typed from
    memory.
    """
    from . import fomc

    path = settings.db_path.parent / "fomc_calendar.json"
    if args.refresh:
        meetings = fomc.fetch_calendar(settings.http_timeout_seconds)
        fomc.save_calendar(meetings, path)
        print(f"saved {len(meetings)} meetings to {path}")
    else:
        meetings = fomc.load_calendar(path)
        print(f"{len(meetings)} meetings, fetched {fomc.calendar_fetched_at(path)}")

    today = datetime.now(ZoneInfo(settings.snapshot_tz)).date()
    upcoming = [m for m in meetings if m.effective > today][:8]
    print(f"\nnext {len(upcoming)} meetings "
          f"(effective = decision + {fomc.EFFECTIVE_LAG_DAYS}d):")
    for m in upcoming:
        print(f"  {m.start} .. {m.end}   effective {m.effective:%Y-%m-%d %a}")
    return 0


def cmd_policy(args: argparse.Namespace, settings: Settings) -> int:
    """Solve the implied policy path from a ZQ settlement file (spec 2.1).

    Settlements come from a local file, not from cmegroup.com: automated access
    to their settlements endpoint is prohibited by CME's Data Terms of Use, and
    it carries only about a week of history regardless. See adapters/cme.py.
    """
    from dataclasses import replace

    from . import fomc, policy
    from .adapters.cme import CmeFileAdapter, parse_contract_month
    from .store.query import get

    calendar_path = settings.db_path.parent / "fomc_calendar.json"
    meetings = fomc.load_calendar(calendar_path)

    adapter = CmeFileAdapter(snapshot_tz=settings.snapshot_tz)
    trade_date = args.trade_date
    raw_rows = adapter.parse_file(Path(args.settlements).read_text(encoding="utf-8"))
    obs = adapter.to_observations(raw_rows, args.root, trade_date, "pending")

    settles: dict[date, float] = {}
    for row in raw_rows:
        month = parse_contract_month(row.get("month", ""))
        if month is None:
            continue
        raw = str(row.get("settle", "")).replace(",", "").strip()
        if raw and raw not in {"-", "."}:
            settles[month] = float(raw)

    with Store(settings.db_path) as store:
        if args.spot is not None:
            spot = args.spot
            spot_note = "supplied on the command line"
        else:
            effr = get(store.conn, "rates.effr", end=trade_date)
            if effr.empty:
                raise XactxError(
                    "policy: no rates.effr observations to anchor the path. "
                    "Run `xactx ingest --source fred` first, or pass --spot."
                )
            spot = float(effr.iloc[-1]["value"])
            spot_note = f"rates.effr on {effr.iloc[-1]['value_date']}"

        path = policy.solve_path(settles, meetings, spot, trade_date)

        thin_used = sorted(
            set(adapter.thin_contracts(raw_rows))
            & {m.contract_month for m in path.meetings}
        )

        print(f"trade date {trade_date}   spot {spot:.4f}% ({spot_note})")
        print(f"calendar fetched {fomc.calendar_fetched_at(calendar_path)}")
        print(f"strip: {len(settles)} contract months")
        print()
        print(f"{'#':>2} {'effective':11s} {'implied':>9s} {'chg':>7s} {'cum':>7s} "
              f"{'+-bp':>5s} {'amp':>5s}   probabilities (two-outcome convention)")
        for i, mr in enumerate(path.meetings):
            probs = ", ".join(
                f"{k * 25:+d}bp {v * 100:.0f}%"
                for k, v in sorted(mr.probabilities().items())
            )
            print(f"{i + 1:>2} {mr.meeting.effective!s:11s} {mr.rate_pct:8.3f}% "
                  f"{mr.change_bp:+6.1f} {path.cumulative_change_bp(i):+6.1f} "
                  f"{mr.rounding_uncertainty_bp:5.1f} {mr.amplification:4.1f}x   {probs}")
        print("  +-bp bounds settlement-tick rounding only, amplified by amp = "
              "month days /")
        print("  days after the meeting, plus the fit residual. Not a confidence "
              "interval.")

        if thin_used:
            months = [f"{m:%Y-%m}" for m in thin_used]
            print()
            print(f"WARNING: the path chains through thinly traded months: {months}")
        print()
        print(f"joint least-squares fit; worst monthly residual "
              f"{path.fit_residual_bp:.2f}bp")
        for n in path.notes:
            print(f"  note: {n}")
        for w in path.warnings:
            print()
            print(f"WARNING: {w}")
        print()
        print("NOTE: these probabilities are UNVALIDATED against CME FedWatch. "
              "See policy.py for why that comparison cannot be built.")

        if args.store:
            loader = Loader(store)
            for meta in adapter.discovered.values():
                loader.register_series(meta)
            batch = loader.start_batch("cme", f"{args.root} {trade_date}")
            try:
                loader.persist([replace(o, source_batch=batch) for o in obs])
                loader.persist(policy.path_observations(path, batch))
                loader.finish_batch(batch, "ok")
                print(f"\nstored the strip and the solved path under batch {batch}")
            except Exception as e:
                loader.finish_batch(batch, "failed", str(e)[:500])
                raise
    return 0


def cmd_board(args: argparse.Namespace, settings: Settings) -> int:
    """Spec 3.1. Defaults to the latest known state; --as-of rebuilds the board
    as it would have looked at that moment."""
    from .analytics import BoardParams, build_board

    tz = ZoneInfo(settings.snapshot_tz)
    as_of = args.as_of or datetime.now(tz)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=tz)

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

    with Store(settings.db_path, read_only=True) as store:
        board = build_board(store.conn, params)

    if board.empty:
        print("no series produced a row")
        return 1

    ok = board[board["status"] == "ok"]
    print(f"as of {as_of.isoformat()}   window {params.zscore_window} "
          f"(excludes the scored change)")
    print()
    print(f"{'series_id':22s} {'date':10s} {'change':>10s} {'unit':6s} "
          f"{'z':>7s} {'%ile':>6s} {'n':>4s} {'vol':12s} stale")
    for r in ok.head(args.top).itertuples():
        flag = f"{r.vol_flag}({r.vol_percentile:.0f})" if r.vol_flag else ""
        stale = f"{r.stale_days}d" if r.stale_days > params.stale_warn_days else ""
        print(f"{r.series_id:22s} {r.value_date!s:10s} {r.change:10.2f} "
              f"{r.change_unit:6s} {r.z:7.2f} {r.percentile:6.1f} {r.window_n:4d} "
              f"{flag:12s} {stale}")

    excluded = board[board["status"] != "ok"]
    if not excluded.empty:
        print()
        print("not scored:")
        for status, grp in excluded.groupby("status"):
            names = ", ".join(grp["series_id"][:6])
            more = " ..." if len(grp) > 6 else ""
            print(f"  {status:22s} {len(grp):3d}  {names}{more}")
    return 0


def cmd_factors(args: argparse.Namespace, settings: Settings) -> int:
    """Rolling factor decomposition and today's attribution (spec 3.2)."""
    from .analytics import attribute, build_panel, fit

    tz = ZoneInfo(settings.snapshot_tz)
    as_of = args.as_of or datetime.now(tz)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=tz)

    with Store(settings.db_path, read_only=True) as store:
        panel = build_panel(
            store.conn,
            as_of,
            window=settings.pca_window,
            max_gap_days=settings.max_gap_days,
            min_series=settings.pca_min_series,
            drop_stale_days=args.drop_stale_days,
        )
        model = fit(panel, settings.pca_components)
        attribution = attribute(panel, model)

    print(f"as of {as_of.isoformat()}")
    print(f"panel: {len(panel.series)} series x {len(panel.frame)} complete rows, "
          f"window {settings.pca_window}")
    print(f"       {panel.dropped_dates} date(s) dropped where any series was "
          f"missing; nothing filled")
    if panel.stale_days:
        print(f"       ends {panel.frame.index[-1]}, {panel.stale_days}d behind the "
              f"freshest data")
        print(f"       capped by {panel.limiting_series[:4]} "
              f"(--drop-stale-days trades them for freshness)")
    print()

    total = float(model.explained_variance_ratio.sum())
    print("factors, labelled post hoc from their loadings:")
    for i, ratio in enumerate(model.explained_variance_ratio):
        print(f"  f{i + 1}  {ratio * 100:5.1f}% of window variance   {model.label(i)}")
    print(f"      {total * 100:5.1f}% cumulative")
    print()

    print(f"attribution for {attribution.value_date}:")
    for i, sh in enumerate(attribution.variance_share):
        print(f"  f{i + 1}  {sh * 100:5.1f}% of today's move   "
              f"score {attribution.scores[i]:+6.2f}")
    print(f"  unexplained {attribution.unexplained_share * 100:5.1f}%")
    if attribution.unexplained_share > 0.5:
        print("  NOTE: the factors explain less than half of today's move. The "
              "move is")
        print("  mostly idiosyncratic; there is no macro story to tell here.")
    print()

    print("largest residuals (standardised units, actual minus fitted):")
    for sid, value in attribution.dominant_residuals(args.top).items():
        print(f"  {sid:24s} {value:+6.2f}   "
              f"actual {attribution.actual[sid]:+6.2f}  "
              f"fitted {attribution.fitted[sid]:+6.2f}")

    for w in model.warnings:
        print()
        print(f"WARNING: {w}")
    return 0


def cmd_regime(args: argparse.Namespace, settings: Settings) -> int:
    """Three-state regime classification (spec 3.4)."""
    from .analytics import classify

    tz = ZoneInfo(settings.snapshot_tz)
    as_of = args.as_of or datetime.now(tz)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=tz)

    with Store(settings.db_path, read_only=True) as store:
        reading = classify(
            store.conn,
            as_of,
            window_days=settings.regime_window_days,
            score_window=settings.regime_score_window,
            min_z=settings.regime_min_z,
            max_gap_days=settings.max_gap_days,
        )

    print(f"as of {as_of.isoformat()}   window {reading.window_days} days")
    print(f"data through {reading.value_date}")
    print()
    print(f"  {reading.state.upper()}")
    print(f"  {reading.detail}")
    print()
    print(f"evidence ({reading.window_days}-day move, in sd of its own history, "
          f"threshold {settings.regime_min_z}):")
    for sid, z in reading.evidence().items():
        moved = "moved" if abs(z) >= settings.regime_min_z else "quiet"
        print(f"  {sid:20s} {z:+6.2f}sd   {moved}")
    return 0


def cmd_edges(args: argparse.Namespace, settings: Settings) -> int:
    """Transmission graph: rolling betas, correlation percentiles and sign
    conflicts (spec 4)."""
    from . import graph

    tz = ZoneInfo(settings.snapshot_tz)
    as_of = args.as_of or datetime.now(tz)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=tz)

    with Store(settings.db_path) as store:
        n_defs = graph.register(store.conn)
        stats, skipped = graph.estimate_all(
            store.conn,
            as_of,
            beta_window=settings.beta_window,
            corr_history=settings.corr_history_window,
            max_gap_days=settings.max_gap_days,
        )
        loader = Loader(store)
        batch = loader.start_batch("graph", as_of.isoformat())
        graph.persist(store.conn, stats, batch)
        loader.finish_batch(batch, "ok")

    print(f"as of {as_of.isoformat()}   beta window {settings.beta_window} "
          f"(correlation percentile against {settings.corr_history_window})")
    print(f"{n_defs} edges defined, {len(stats)} estimated, "
          f"{len(skipped)} not computable")
    print()

    header = (f"{'edge':46s} {'exp':>3s} {'lag':>3s} {'beta':>8s} {'t':>6s} "
              f"{'r2':>5s} {'corr':>6s} {'pct':>5s}  flags")
    print(header)
    for s_ in sorted(stats, key=lambda e: (e.definition.chain,
                                           -abs(e.beta_t_stat or 0))):
        d = s_.definition
        flags = []
        if s_.sign_conflict:
            flags.append("SIGN-CONFLICT")
        if s_.corr_extreme:
            flags.append(f"corr-{s_.corr_extreme}")
        if not s_.significant:
            flags.append("not-significant")
        exp = {1: "+", -1: "-", 0: "?"}[d.expected_sign]
        pair = f"{d.from_series} -> {d.to_series}"
        print(f"{pair:46s} {exp:>3s} {d.typical_lag_days:>3d} {s_.beta:8.3f} "
              f"{s_.beta_t_stat:6.1f} {s_.r_squared:5.2f} {s_.corr:6.2f} "
              f"{s_.corr_percentile:5.0f}  {' '.join(flags)}")

    print()
    print("exp '?' means expected_sign is deliberately 0: the sign is "
          "regime-dependent and")
    print("asserting one would mislead. Read the correlation percentile on "
          "those edges.")

    conflicts = [x for x in stats if x.sign_conflict]
    extremes = [x for x in stats if x.corr_extreme]
    if conflicts:
        print()
        print("SIGN CONFLICTS (beta significant and against the prior):")
        for c in conflicts:
            print(f"  {c.definition.from_series} -> {c.definition.to_series}: "
                  f"expected {c.definition.expected_sign:+d}, beta "
                  f"{c.beta:+.3f} (t {c.beta_t_stat:+.1f})")
            print(f"    {c.definition.note[:100]}")
    if extremes:
        print()
        print("CORRELATION AT AN EXTREME OF ITS OWN HISTORY:")
        for e in extremes:
            print(f"  {e.definition.from_series} -> {e.definition.to_series}: "
                  f"corr {e.corr:+.2f}, {e.corr_percentile:.0f}th percentile of "
                  f"{e.corr_history_n} readings")

    if skipped and args.show_skipped:
        print()
        print("not computable:")
        for (a, b), why in sorted(skipped.items()):
            print(f"  {a} -> {b}")
            print(f"    {why[:130]}")
    elif skipped:
        print()
        print(f"({len(skipped)} edges not computable; --show-skipped for why)")
    return 0


def cmd_brief(args: argparse.Namespace, settings: Settings) -> int:
    """Generate the daily brief (spec 5)."""
    from . import brief

    tz = ZoneInfo(settings.snapshot_tz)
    as_of = args.as_of or datetime.now(tz)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=tz)

    with Store(settings.db_path) as store:
        path, text, unanswered = brief.write(store.conn, as_of, settings, args.out)

    if args.stdout:
        print(text)
    else:
        print(f"wrote {path} ({len(text):,} characters)")
        if unanswered:
            print(f"{unanswered} of the five questions could not be answered; "
                  "each says why in place.")
    return 0


def cmd_status(args: argparse.Namespace, settings: Settings) -> int:
    with Store(settings.db_path, read_only=True) as store:
        rows = store.conn.execute(
            """
            SELECT m.series_id, m.source, m.vintage_source,
                   COUNT(o.value) AS n_obs,
                   COUNT(DISTINCT o.value_date) AS n_dates,
                   MIN(o.value_date) AS first_date,
                   MAX(o.value_date) AS last_date
            FROM series_metadata m
            LEFT JOIN observations o USING (series_id)
            GROUP BY 1, 2, 3
            ORDER BY m.source, m.series_id
            """
        ).fetchall()

        print(f"{'series_id':24s} {'source':9s} {'vintage':15s} "
              f"{'obs':>7s} {'dates':>7s}  range")
        for sid, source, vintage, n_obs, n_dates, first, last in rows:
            rng = f"{first}..{last}" if first else "-"
            # obs > dates means stored revisions: more than one vintage per date.
            print(f"{sid:24s} {source:9s} {vintage:15s} "
                  f"{n_obs:7d} {n_dates:7d}  {rng}")

        print()
        print("as_of provenance (observations.as_of_basis):")
        for basis, n in store.conn.execute(
            "SELECT as_of_basis, COUNT(*) FROM observations GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall():
            print(f"  {basis:16s} {n:8d}")

        total = store.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        revised = store.conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT series_id, value_date FROM observations
                GROUP BY 1, 2 HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        print()
        print(f"{total} observations; {revised} (series, date) pairs hold "
              f"more than one vintage")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xactx",
        description=__doc__,
        # Without this argparse collapses the docstring's newlines and the
        # command list arrives as one run-on paragraph.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("verify", help="check every source code is still live")

    p_ing = sub.add_parser("ingest", help="register the universe and backfill")
    p_ing.add_argument("--source", default="all",
                       choices=["all", "fred", "treasury", "cboe", "cftc"])
    p_ing.add_argument("--since", type=date.fromisoformat,
                       help="first value_date (default: XA_BACKFILL_START)")
    p_ing.add_argument("--until", type=date.fromisoformat,
                       help="last value_date (default: today)")
    p_ing.add_argument("--register-only", action="store_true",
                       help="update series_metadata and exit without fetching")

    sub.add_parser("status", help="what is stored, per series")

    p_der = sub.add_parser("derive", help="compute and store the derived series")
    p_der.add_argument("--target", help="one derivation only (default: all)")
    p_der.add_argument("--as-of", type=datetime.fromisoformat,
                       help="compute as the world looked at this moment")
    p_der.add_argument("--since", type=date.fromisoformat)
    p_der.add_argument("--until", type=date.fromisoformat)

    p_fomc = sub.add_parser("fomc", help="FOMC meeting calendar (spec 2.1)")
    p_fomc.add_argument("--refresh", action="store_true",
                        help="re-fetch from federalreserve.gov")

    p_pol = sub.add_parser("policy",
                           help="implied policy path from a ZQ settlement file")
    p_pol.add_argument("settlements", help="local CSV or JSON settlement file")
    p_pol.add_argument("--trade-date", type=date.fromisoformat, required=True)
    p_pol.add_argument("--root", default="ZQ")
    p_pol.add_argument("--spot", type=float,
                       help="spot effective rate; defaults to stored rates.effr")
    p_pol.add_argument("--store", action="store_true",
                       help="persist the strip and the solved path")

    p_fac = sub.add_parser("factors", help="factor decomposition (spec 3.2)")
    p_fac.add_argument("--as-of", type=datetime.fromisoformat)
    p_fac.add_argument("--top", type=int, default=6,
                       help="residuals to show (default 6)")
    p_fac.add_argument("--drop-stale-days", type=int,
                       help="exclude series whose newest observation is more than "
                            "this many days behind the freshest; trades coverage "
                            "for freshness")

    p_reg = sub.add_parser("regime", help="regime classification (spec 3.4)")
    p_reg.add_argument("--as-of", type=datetime.fromisoformat)

    p_edge = sub.add_parser("edges", help="transmission graph (spec 4)")
    p_edge.add_argument("--as-of", type=datetime.fromisoformat)
    p_edge.add_argument("--show-skipped", action="store_true",
                        help="explain each edge that could not be computed")

    p_brief = sub.add_parser("brief", help="generate the daily brief (spec 5)")
    p_brief.add_argument("--as-of", type=datetime.fromisoformat)
    p_brief.add_argument("--out", default="reports",
                         help="directory to write into (default: reports/)")
    p_brief.add_argument("--stdout", action="store_true",
                         help="also print the brief")

    p_board = sub.add_parser("board", help="normalized change board (spec 3.1)")
    p_board.add_argument("--as-of", type=datetime.fromisoformat,
                         help="rebuild the board as of this moment (ISO 8601); "
                              "default now, in the snapshot timezone")
    p_board.add_argument("--top", type=int, default=15,
                         help="rows to show, ranked by |z| (default 15)")

    args = parser.parse_args(argv)
    settings = load_settings()
    configure(settings.log_level)

    handlers = {
        "verify": cmd_verify,
        "ingest": cmd_ingest,
        "status": cmd_status,
        "derive": cmd_derive,
        "fomc": cmd_fomc,
        "policy": cmd_policy,
        "board": cmd_board,
        "factors": cmd_factors,
        "regime": cmd_regime,
        "edges": cmd_edges,
        "brief": cmd_brief,
    }
    try:
        return handlers[args.command](args, settings)
    except XactxError as e:
        log.error("%s: %s", type(e).__name__, e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
