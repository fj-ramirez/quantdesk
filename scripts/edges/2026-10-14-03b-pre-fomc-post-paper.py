"""Context for #524-525 (not counted): pre-FOMC split at the paper's sample end (2011), plus the
broker span on S&P.fs / NAS100.fs 16:00 closes. 2020+ dates come from Wikipedia's FOMC action
table (scheduled meetings only); 2012-2019 from the Fed's historical pages, as in #524-525."""
import io
import re
import sys

import pandas as pd
import requests
from _common import closes_for
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
UA = {"User-Agent": "Mozilla/5.0 quantdesk research"}
dates = set()
for year in range(1994, 2020):
    html = requests.get(f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm", headers=UA, timeout=30).text
    for m1, d1, m2, d2, y in HEAD.findall(html):
        dates.add(pd.Timestamp(f"{(m2 or m1).strip()} {d2 or d1} {y}"))
w = pd.read_html(io.StringIO(requests.get(
    "https://en.wikipedia.org/wiki/History_of_Federal_Open_Market_Committee_actions", headers=UA, timeout=30).text))[1]
w = w[~w["Notes"].astype(str).str.contains("emergency|unscheduled|conference call", case=False)]
dates |= {d for d in pd.to_datetime(w["Date"]) if d.year >= 2020}
dates = sorted(dates)

for k, (ticker, cfd) in enumerate(PAIRS):
    c = yahoo_closes(ticker, "1993-12-01", "2026-10-10")
    days = [d for d in dates if d in c.index]
    tr = close_to_close(c, [previous_session(c.index, d) for d in days], days, spread_pct(data, cfd))
    for lo, hi in (("1994", "2011"), ("2012", "2019"), ("2020", "2026")):
        part = tr[(tr.t0 >= lo) & (tr.t0 < str(int(hi) + 1))]
        summarize(f"  ctx {lo}-{hi}", ticker, part.reset_index(drop=True), 525)
    cc = closes_for(data, cfd)
    idx = cc.index.tz_localize(None).normalize()
    s = pd.Series(cc["close"].to_numpy(), index=idx)
    days = [d for d in dates if d in s.index and d >= idx[1]]
    summarize("  ctx broker 2019+", cfd, close_to_close(s, [previous_session(s.index, d) for d in days], days, spread_pct(data, cfd)), 525)
