"""The desk's own dealer gamma, read from `gex.gex_levels` (T170, plans/sentiment).

T91's pattern again: another module's table, crossed at the one seam the terminal has for
"somewhere data comes from". The conditions reading (T171) needs dealer gamma beside the macro
regime and sentiment, as of any instant, and the terminal's point-in-time machinery is the only
place that question already has an answer.

Source codes are `<UNDERLYING>:<field>`:

    net        net GEX over all expiries (filter ALL), the engine's signed dollar figure
    flip_dist  (spot - flip) / spot, in percent; positive = spot above the flip

One value per session: the session's **EOD** snapshot (`is_eod`), the latest one if a session
has several. Intraday snapshots are never mixed in -- a 10:00 reading next to a 16:20 one would
make the series' day-to-day changes mean two different things.

as_of is `gex_levels.computed_at`, not `snapshots.captured_at`: the number did not exist until
the engine computed it. For a live capture the two are seconds apart; for a recompute or a
backfill the later time is the honest one, and a recompute then *adds* a row (invariant 10)
instead of colliding with the first.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.core.schemas import SCHEMA_GEX

from ..errors import EmptyFetchError, UnknownSeriesError
from ..models import Observation
from ..store.db import Store
from .base import BaseAdapter

FIELDS = frozenset({"net", "flip_dist"})

#: The expiry filter read. ALL is the headline number every GEX page shows.
FILTER = "ALL"


def parse_code(code: str) -> tuple[str, str]:
    underlying, _, field = code.partition(":")
    if not underlying or field not in FIELDS:
        raise UnknownSeriesError(
            f"desk_gex: {code!r} is not <UNDERLYING>:<field> with field in {sorted(FIELDS)}"
        )
    return underlying, field


class DeskGexAdapter(BaseAdapter):
    name = "desk_gex"

    def resolve(self, source_code: str) -> str:
        parse_code(source_code)
        return super().resolve(source_code)

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
            underlying, field = parse_code(code)
            out.extend(self._read(underlying, field, series_id, start, end, source_batch))
        return out

    def _read(
        self, underlying: str, field: str, series_id: str, start: date, end: date, batch: str
    ) -> list[Observation]:
        # Schema-qualified: the terminal connection's search_path is `terminal` (invariant 8).
        sql = f"""
            SELECT DISTINCT ON (s.session_date)
                   s.session_date, l.computed_at, l.net_gex, l.flip_point, l.spot
            FROM {SCHEMA_GEX}.snapshots s
            JOIN {SCHEMA_GEX}.gex_levels l ON l.snapshot_id = s.id
            WHERE s.underlying = ? AND s.is_eod AND l.filter = ?
              AND s.session_date BETWEEN ? AND ?
            ORDER BY s.session_date, s.captured_at DESC
        """
        with Store(read_only=True) as store:
            rows = store.conn.execute(sql, [underlying, FILTER, start, end]).fetchall()

        obs: list[Observation] = []
        for session_date, computed_at, net_gex, flip_point, spot in rows:
            value = self.value(field, net_gex, flip_point, spot)
            if value is None:
                continue
            obs.append(
                Observation(
                    series_id=series_id,
                    value_date=session_date,
                    as_of=computed_at,
                    value=value,
                    source_batch=batch,
                    as_of_basis="derived_lag",
                )
            )
        if not obs:
            raise EmptyFetchError(
                f"desk_gex: {underlying}:{field} ({series_id}) has no EOD {FILTER} levels in "
                f"{SCHEMA_GEX}.gex_levels between {start} and {end}. The gex-capture worker "
                f"fills it -- check `GET /api/gex/health/capture`."
            )
        return obs

    @staticmethod
    def value(
        field: str, net_gex: float | None, flip_point: float | None, spot: float | None
    ) -> float | None:
        """The stored number, or None when its inputs are unknown (never a fabricated 0)."""
        if field == "net":
            return None if net_gex is None else float(net_gex)
        if flip_point is None or not spot:
            return None
        return (spot - flip_point) / spot * 100.0
