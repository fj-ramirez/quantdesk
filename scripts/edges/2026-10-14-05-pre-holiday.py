"""Backlog 5: pre-holiday effect (Ariel 1990; Kim and Park 1994), on independent history.

Unblocked 2026-10-14: the broker span holds <= 65 holidays, so the test runs on ^GSPC / ^NDX
daily closes 1990-01 -> 2019-06 instead (about 270 holidays).
Rule as declared: long the close two sessions before a US market holiday to the close of the
session before it. A holiday is a weekday with no session, except closures nobody knew in
advance (listed below). Ledger tests 529-530.
"""
import sys

import pandas as pd
from _independent import (
    PAIRS,
    close_to_close,
    nyse_holidays,
    previous_session,
    spread_pct,
    summarize,
    yahoo_closes,
)

data = sys.argv[1]
UNSCHEDULED = {pd.Timestamp(d) for d in (
    "1994-04-27",                                           # Nixon funeral
    "2001-09-11", "2001-09-12", "2001-09-13", "2001-09-14",  # 9/11
    "2004-06-11",                                           # Reagan funeral
    "2007-01-02",                                           # Ford funeral
    "2012-10-29", "2012-10-30",                             # Hurricane Sandy
    "2018-12-05",                                           # G.H.W. Bush funeral
)}

for k, (ticker, cfd) in enumerate(PAIRS):
    c = yahoo_closes(ticker, "1990-01-01")
    hol = [h for h in nyse_holidays(c.index, UNSCHEDULED) if h > c.index[5]]
    # a long weekend counts once: keep the first closed weekday of each run
    hol = [h for i, h in enumerate(hol) if i == 0 or (h - hol[i - 1]).days > 3]
    e = [previous_session(c.index, h, 2) for h in hol]
    x = [previous_session(c.index, h, 1) for h in hol]
    print(f"{ticker}: {len(hol)} holidays")
    summarize("5 pre-holiday (1990-2019)", ticker, close_to_close(c, e, x, spread_pct(data, cfd)), 529 + k)
