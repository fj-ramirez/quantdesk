"""Backlog 14: golden cross (Brock, Lakonishok, LeBaron 1992).

Long while SMA50 > SMA200 of 16:00 closes, otherwise flat. Position rule. Ledger tests 330-333.
"""
import sys

from _common import INDICES, closes_for, eval_positions

data = sys.argv[1]
for i, sym in enumerate(INDICES + ["XAUUSD"], start=1):
    c = closes_for(data, sym)
    pos = (c["close"].rolling(50).mean() > c["close"].rolling(200).mean()).astype(float)
    eval_positions("14 golden cross 50/200", sym, c, pos, 329 + i)
