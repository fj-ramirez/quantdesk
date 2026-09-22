"""ETF proxy prices, read from `gex.daily_bars` (T91).

Three series the transmission graph declares and five edges reference had **zero**
observations: `eq.rut`, `eq.msci_em` and `cmdty.gold`. Each was registered as pending against
a "prices adapter, phase 4" that did not exist, because none of them is on FRED -- RU2000PR is
retired, MSCI EM is licensed, and both LBMA gold fixes were withdrawn (see
`universe.RETIRED_CODES`).

Meanwhile the desk has been capturing IWM, EEM and GLD daily bars for five years, in the same
Postgres instance, one schema over. This adapter is that observation, made into an adapter:
**the prices source is the desk's own bars table.** No new vendor, no new credential, no new
failure mode -- the one job that fills it already runs nightly and is watched.

**It is shaped like every other adapter and it reads SQL instead of HTTP.** That is the whole
design: `modules/terminal` knows nothing about `modules/gex` and should not start now, so the
crossing happens at the one seam terminal already has for "somewhere data comes from". A
`SELECT` against a schema-qualified table is the smallest possible version of that.

**An ETF is not the index it tracks.** IWM carries tracking error, an expense ratio and a
distribution the Russell 2000 does not; GLD's share price is a fraction of an ounce net of
accrued fees, not the gold spot. For the log-return correlations and betas the graph computes,
that is immaterial -- the tracking error is tiny next to the daily move. For anyone reading
the series as a *level* it is not immaterial at all, which is why `universe` records the proxy
instrument in `series_metadata.notes` and the display name says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time

from app.core.schemas import SCHEMA_GEX

from ..errors import EmptyFetchError, UnknownSeriesError
from ..models import Observation
from ..store.db import Store
from .base import BaseAdapter

#: The close is an exchange-local 16:00 fact, but it does not reach this table until
#: `app.modules.gex.jobs.scheduler`'s bars job runs at 17:30 ET. 17:30 is therefore the
#: honest earliest moment the desk could have read it, and an `as_of` must never claim
#: knowledge earlier than the knowledge existed -- the whole point of invariant 10.
PUBLICATION_LOCAL_TIME = time(17, 30)

#: The column taken, stated once. `close` and not `adj_close`: the graph consumes log returns
#: over a 250-day window, and an adjusted series silently rewrites its own history every time
#: a distribution is paid, which is exactly the kind of retroactive edit the point-in-time
#: rule exists to forbid.
VALUE_COLUMN = "close"


class PricesAdapter(BaseAdapter):
    """Daily closes for the ETF proxies, straight out of `gex.daily_bars`."""

    name = "prices"

    def fetch(
        self,
        series_codes: Sequence[str],
        start: date,
        end: date,
        source_batch: str,
    ) -> list[Observation]:
        out: list[Observation] = []
        for code in series_codes:
            series_id = self.resolve(code)
            out.extend(self._read(code, series_id, start, end, source_batch))
        return out

    def _read(
        self,
        code: str,
        series_id: str,
        start: date,
        end: date,
        source_batch: str,
    ) -> list[Observation]:
        # **Schema-qualified, deliberately.** `store.db.connect()` pins `search_path` to the
        # terminal schema so that every query in this module can name `observations` bare;
        # an unqualified `daily_bars` here would therefore not be found at all.
        sql = f"""
            SELECT date, {VALUE_COLUMN}
            FROM {SCHEMA_GEX}.daily_bars
            WHERE symbol = ? AND date BETWEEN ? AND ?
            ORDER BY date
        """
        # Its own read-only Store rather than one handed in: an adapter is constructed with
        # nothing but its series map (adapters/base.py), and threading a connection through
        # `build_adapter` for one source would make every other adapter's signature carry a
        # database it never touches.
        with Store(read_only=True) as store:
            rows = store.conn.execute(sql, [code, start, end]).fetchall()

        obs = [
            Observation(
                series_id=series_id,
                value_date=value_date,
                as_of=self.at_local(value_date, PUBLICATION_LOCAL_TIME),
                value=float(close),
                source_batch=source_batch,
                as_of_basis="derived_lag",
            )
            for value_date, close in rows
            if close is not None
        ]

        if not obs:
            # The adapter contract (adapters/base.py): never return an empty list, because a
            # silently truncated series only shows up later as an inexplicable z-score. Here
            # it would most likely mean the symbol was dropped from `SCAN_UNIVERSE`, which is
            # a real change worth failing on rather than absorbing.
            raise EmptyFetchError(
                f"prices: {code} ({series_id}) has no {VALUE_COLUMN} in "
                f"{SCHEMA_GEX}.daily_bars between {start} and {end}. The bars job fills "
                f"that table -- check `GET /api/gex/health/capture` and that {code} is "
                f"still in SCAN_UNIVERSE."
            )
        return obs

    def resolve(self, source_code: str) -> str:
        try:
            return super().resolve(source_code)
        except UnknownSeriesError:
            raise UnknownSeriesError(
                f"prices: {source_code!r} is not a symbol this adapter was given. It reads "
                f"{SCHEMA_GEX}.daily_bars, so a new proxy needs a universe entry *and* the "
                f"symbol in the bars job's SCAN_UNIVERSE. Known: {sorted(self.series_map)}"
            ) from None
