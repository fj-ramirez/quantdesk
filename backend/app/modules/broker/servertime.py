"""The broker's clock (T144, plans/charter-mt5/README.md decision 3). Pure: no I/O, no clock.

MT5 stamps bars and ticks in the broker's **server wall-clock**, encoded as epoch numbers that
are not UTC. Axi, like most MT5 brokers, runs its servers on the "New York close" convention:
server time is New York time plus seven hours, so the New York 17:00 close is 00:00 on the
server and every daily bar is one New York trading session. That gives UTC+2 in US winter and
UTC+3 in US summer, switching on the **US** DST dates.

The convention is a *model*, and the desk does not take it on trust:

* :func:`measure_offset` reads today's offset from a fresh tick against the bridge's UTC
  clock. A measurement only describes the present.
* :func:`model_offset` is what the convention says the offset was at any instant, including
  last summer, which is what a backfill needs.
* The ingest worker applies the model only while the latest fresh measurement **agrees** with
  it, and stops writing when they disagree. That is the measured-not-configured rule of
  decision 3, extended to history.

Both directions go through New York wall time with `zoneinfo`, so DST is handled where it
lives. The ambiguous and skipped hours of a DST switch fall at 01:00–03:00 New York on a
Sunday, when none of the instruments trade, so no traded bar is ever ambiguous.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

__all__ = [
    "MEASURE_TOLERANCE_S",
    "NY",
    "SERVER_MINUS_NY",
    "measure_offset",
    "model_offset",
    "server_epoch_to_utc",
    "utc_to_server_epoch",
]

NY = ZoneInfo("America/New_York")

#: Server wall-clock minus New York wall-clock under the New York close convention.
SERVER_MINUS_NY = dt.timedelta(hours=7)

#: A measurement is taken from a tick that is at most about a minute old. Offsets are whole
#: quarter-hours, so a raw difference more than this far from one is not a clean reading.
MEASURE_TOLERANCE_S = 90


def model_offset(at: dt.datetime) -> int:
    """Server-minus-UTC in seconds at instant `at` (tz-aware), under the convention."""
    if at.tzinfo is None:
        raise ValueError("model_offset needs a tz-aware datetime")
    ny_offset = at.astimezone(NY).utcoffset()
    assert ny_offset is not None
    return int((ny_offset + SERVER_MINUS_NY).total_seconds())


def server_epoch_to_utc(server_epoch: float) -> dt.datetime:
    """An MT5 server-time epoch (seconds) as the tz-aware UTC instant it names."""
    server_wall = dt.datetime.fromtimestamp(server_epoch, tz=dt.UTC).replace(tzinfo=None)
    return (server_wall - SERVER_MINUS_NY).replace(tzinfo=NY).astimezone(dt.UTC)


def utc_to_server_epoch(at: dt.datetime) -> int:
    """The MT5 server-time epoch (seconds) for tz-aware instant `at`."""
    return int(at.timestamp()) + model_offset(at)


def measure_offset(tick_server_ms: int, bridge_utc_ms: int) -> int | None:
    """Server-minus-UTC in seconds, read from a fresh tick, rounded to a quarter-hour.

    The caller must know the tick is fresh. A tick from Friday's close, read on Saturday, would
    round to a wrong whole quarter-hour about one time in ten. The worker only measures from a
    tick that has changed since its previous poll.

    Returns `None` when the raw difference is not within :data:`MEASURE_TOLERANCE_S` of a
    quarter-hour, because then the reading is not clean.
    """
    raw = (tick_server_ms - bridge_utc_ms) / 1000
    rounded = round(raw / 900) * 900
    if abs(raw - rounded) > MEASURE_TOLERANCE_S:
        return None
    return int(rounded)
