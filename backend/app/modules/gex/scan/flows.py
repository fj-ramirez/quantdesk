"""ETF flow computation -- pure module (T52, plans/continuation/05-etf-flows.md).

**Pure, exactly like `app.modules.gex.gex.engine` and `app.modules.gex.scan.breakouts`/`app.modules.gex.scan.trend`**: no HTTP, DB,
filesystem or logging. `compute_flows` takes two already-fetched wide frames (the
`read_universe_shares_outstanding`/`read_universe_nav` shape from
`app.modules.gex.storage.flows_repository`) and returns a plain `DataFrame`; `app.modules.gex.api.scan` is where the
repository reads happen, the same caller/pure split every other `app.modules.gex.scan.*` module uses.

**Flow definitions, verbatim from the plan (`plans/continuation/05-etf-flows.md`)**:

    flow_t = (SO_t - SO_{t-1}) * NAV_t
    flow_pct_t = flow_t / (SO_{t-1} * NAV_t)

A creation or redemption changes shares outstanding; multiplying that day's *change* by that
day's NAV converts a share-count delta into a dollar flow -- this is the "honest version of a
money-flow indicator" the plan insists on, never a volume/price proxy (Chaikin money flow,
OBV) substituted in silently.

**Window aggregates are a real sum of daily flows, not a shortcut.** `flow_t` uses a different
NAV on every day, so summing `w` days of `flow_t` does not telescope down to
`(SO_end - SO_start) * NAV_end` -- that shortcut would misprice every day in the window except
the last at the wrong NAV. `compute_flows` sums the actual daily series instead.

**Percent aggregates divide by the window-start AUM** (plan, verbatim): `SO` and `NAV` as of
the day *before* the window's first counted flow, i.e. the same "start" point the window's
first `flow_t` used as its own `SO_{t-1}`. This is deliberately the window's opening AUM, not
its closing AUM or an average -- a $1 flow means something different as a fraction of a fund
that started the window at $10B versus $100M, and the opening balance is what the fund actually
held before the window's activity occurred.

**A window is computed only when it can be, and never approximated from less history.** Per
the plan's own accumulation caveat ("compute a 20-day flow only when 20 days exist"), a window
of `w` days needs `w + 1` valid, paired `(SO, NAV)` observations (`w` daily flows, each of which
needs the prior day's `SO`) -- fewer than that yields `None`, not a partial-window number
silently mislabeled as the real thing. `history_since` (the earliest paired observation) is
returned for every symbol regardless, so a caller can render "history since <date>" instead of
a number, per the plan's "What the user sees."
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

__all__ = ["compute_flows"]


def _paired_series(so: pd.Series, nav: pd.Series) -> pd.DataFrame:
    """One symbol's `SO`/`NAV` columns, restricted to dates where both are known, sorted
    ascending by date, with `flow` computed for every row after the first (`NaN` on the
    first row -- there is no prior `SO` to diff against). A date where either `SO` or `NAV`
    is missing is dropped entirely rather than forward-filled -- fabricating a value for a day
    the issuer did not publish would silently invent a flow that never happened.
    """
    aligned = pd.DataFrame({"so": so, "nav": nav}).dropna().sort_index()
    aligned["flow"] = aligned["so"].diff() * aligned["nav"]
    return aligned


def compute_flows(
    so_frame: pd.DataFrame, nav_frame: pd.DataFrame, windows: Sequence[int]
) -> pd.DataFrame:
    """Per-symbol flow and flow-percent aggregates over each window in `windows`.

    Args:
        so_frame: Wide shares-outstanding frame, index = date ascending, one column per
            symbol -- the `app.modules.gex.storage.flows_repository.read_universe_shares_outstanding`
            shape. A symbol with no data at all is still expected as a column (that
            repository function guarantees it, filled `NA`); this function tolerates a symbol
            missing from `so_frame` entirely too, treating it as "no history."
        nav_frame: Wide NAV frame, same index/column shape as `so_frame` -- the
            `read_universe_nav` shape. Only dates present in *both* frames for a given symbol
            (see `_paired_series`) ever contribute a flow.
        windows: Window lengths in trading days, e.g. `[5, 20, 60]`.

    Returns:
        One row per symbol in `so_frame.columns`, with columns:

        * `symbol`
        * `history_since` -- the earliest date with a paired `(SO, NAV)` observation for this
          symbol, or `None` if it has none at all.
        * `latest_date` -- the most recent such date, or `None`.
        * `flow_{w}` / `flow_pct_{w}` for every `w` in `windows` -- `None` when fewer than
          `w + 1` paired observations exist (see module docstring).
    """
    windows = list(windows)
    rows: list[dict] = []

    for symbol in so_frame.columns:
        so = so_frame[symbol]
        nav = nav_frame[symbol] if symbol in nav_frame.columns else pd.Series(dtype=float)
        aligned = _paired_series(so, nav)

        row: dict = {
            "symbol": symbol,
            "history_since": aligned.index[0] if not aligned.empty else None,
            "latest_date": aligned.index[-1] if not aligned.empty else None,
        }

        for w in windows:
            flow_value: float | None = None
            flow_pct: float | None = None
            if len(aligned) >= w + 1:
                window = aligned.iloc[-w:]
                flow_value = float(window["flow"].sum())
                start_so = float(aligned["so"].iloc[-(w + 1)])
                start_nav = float(aligned["nav"].iloc[-(w + 1)])
                start_aum = start_so * start_nav
                flow_pct = None if start_aum == 0 else flow_value / start_aum
            row[f"flow_{w}"] = flow_value
            row[f"flow_pct_{w}"] = flow_pct

        rows.append(row)

    columns = ["symbol", "history_since", "latest_date"]
    for w in windows:
        columns.extend([f"flow_{w}", f"flow_pct_{w}"])
    return pd.DataFrame(rows, columns=columns)
