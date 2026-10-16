"""Backlog 3: pre-FOMC drift (Lucca and Moench 2015), daily version, on independent history.

Unblocked 2026-10-14: the broker span holds <= 47 scheduled meetings, so the test runs on
^GSPC / ^NDX daily closes 1994-01 -> 2019-06 instead (about 200 meetings).
Rule: long from the close of the session before a **scheduled** FOMC announcement to the close
of the announcement day. The paper measures 14:00 -> 14:00; the daily close also carries the
14:15 reaction, which is noted with the result. Unscheduled meetings and conference calls are
excluded. Dates: the Fed's historical pages (federalreserve.gov/monetarypolicy/fomchistoricalYYYY.htm),
announcement = the meeting's last day. Ledger tests 524-525.
"""
import re
import sys

import pandas as pd
import requests
from _independent import (
    PAIRS,
    close_to_close,
    previous_session,
    spread_pct,
    summarize,
    yahoo_closes,
)

data = sys.argv[1]
HEAD = re.compile(r"<h5[^>]*>\s*([A-Z][a-z]+) (\d+)(?:-([A-Z][a-z]+ )?(\d+))? Meeting - (\d{4})\s*</h5>")

dates = []
for year in range(1994, 2020):
    html = requests.get(f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm",
                        headers={"User-Agent": "Mozilla/5.0 quantdesk research"}, timeout=30).text
    for m1, d1, m2, d2, y in HEAD.findall(html):
        month = (m2 or m1).strip()
        day = d2 or d1
        dates.append(pd.Timestamp(f"{month} {day} {y}"))
dates = sorted(set(dates))
print(f"{len(dates)} scheduled meetings {dates[0].date()} -> {dates[-1].date()}")

for k, (ticker, cfd) in enumerate(PAIRS):
    c = yahoo_closes(ticker, "1993-12-01")
    days = [d for d in dates if d in c.index and d <= pd.Timestamp("2019-06-30")]
    prev = [previous_session(c.index, d) for d in days]
    tr = close_to_close(c, prev, days, spread_pct(data, cfd))
    summarize("3 pre-FOMC (1994-2019)", ticker, tr, 524 + k)
