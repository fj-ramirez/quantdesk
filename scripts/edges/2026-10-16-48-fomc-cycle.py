"""Backlog 48: even weeks of the FOMC cycle (Cieslak, Morse and Vissing-Jorgensen 2019, JF).

Trading days from each scheduled announcement (day 0). Long the close-to-close return of day d
when d is in week 0 (days -1..3), 2 (9..13), 4 (19..23) or 6 (29..33). The 5 days before the next
meeting (-6..-2) are odd week -1, so flat. Position rule on 2019-07 -> 2026-10, after the
paper's sample (1994-2016). Ledger 543-544.
"""
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from _common import closes_for, eval_positions

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.modules.terminal.fomc import fetch_calendar

data = sys.argv[1]
HEAD = re.compile(r"<h5[^>]*>\s*([A-Z][a-z]+) (\d+)(?:-([A-Z][a-z]+ )?(\d+))? Meeting - (\d{4})\s*</h5>")
dates = []
for year in (2019, 2020):
    html = requests.get(f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm",
                        headers={"User-Agent": "Mozilla/5.0 quantdesk research"}, timeout=30).text
    for m1, d1, m2, d2, y in HEAD.findall(html):
        dates.append(pd.Timestamp(f"{(m2 or m1).strip()} {d2 or d1} {y}"))
dates += [pd.Timestamp(m.end) for m in fetch_calendar() if m.scheduled]
dates = sorted(set(dates))
print(f"{len(dates)} scheduled meetings {dates[0].date()} -> {dates[-1].date()}")

EVEN = set(range(-1, 4)) | set(range(9, 14)) | set(range(19, 24)) | set(range(29, 34))
for k, sym in enumerate(["S&P.fs", "NAS100.fs"]):
    c = closes_for(data, sym)
    days = c.index.tz_localize(None) if c.index.tz is not None else c.index
    ann = [int(days.get_loc(d)) for d in dates if d in days]
    even = np.zeros(len(days))
    for j, a in enumerate(ann):
        nxt = ann[j + 1] if j + 1 < len(ann) else len(days) + 10
        for i in range(max(a - 1, 0), min(nxt - 1, len(days))):
            if i - a in EVEN and i < nxt - 6:
                even[i] = 1.0
    # day d's return is held from close d-1, so the position at close d-1 is even[d]
    pos = pd.Series(np.r_[even[1:], 0.0], index=c.index)
    print(f"   {sym}: {len(ann)} meetings in span, even-week days {int(even.sum())} of {len(days)}")
    eval_positions("48 FOMC cycle even weeks", sym, c, pos, 543 + k)
