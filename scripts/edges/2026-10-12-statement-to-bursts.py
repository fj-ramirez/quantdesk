"""Turn an Axi MetaTrader HTML statement into the burst list the eurusd-burst-fade scripts read.

A "burst" = EURUSD orders opened within 5 s of each other in the same direction (the copier
splits one provider trade into many 0.01-lot orders). Times stay in **server time (UTC+3 in
summer)**, as in the statement; the analysis scripts subtract 3 h.

    uv run --directory backend python ../scripts/edges/2026-10-12-statement-to-bursts.py \
        ../Statement.htm data/broker-export/burst-entries.csv

The statement carries the account holder's name and number: keep it out of the repo. The output
CSV holds neither.
"""
import html
import re
import sys

import numpy as np
import pandas as pd

src, out = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8", errors="replace").read()
rows = []
for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S):
    cells = [html.unescape(re.sub("<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
    if len(cells) == 14 and cells[0].isdigit():  # closed-trade rows; balance rows have 5 cells
        rows.append(cells)
d = pd.DataFrame(rows, columns="ticket open_time type size item open_price sl tp close_time close_price commission taxes swap profit".split())
for c in "size open_price sl tp close_price commission taxes swap profit".split():
    d[c] = pd.to_numeric(d[c].str.replace(" ", ""), errors="coerce")
d["ot"] = pd.to_datetime(d["open_time"], format="%Y.%m.%d %H:%M:%S")
d["ct"] = pd.to_datetime(d["close_time"], format="%Y.%m.%d %H:%M:%S")
d["sgn"] = np.where(d["type"] == "buy", 1, -1)
d["pips"] = (d["close_price"] - d["open_price"]) * d["sgn"] / np.where(d["item"].str.contains("jpy"), 0.01, 0.0001)
d["net"] = d["profit"] + d["swap"] + d["commission"]
d["hold_h"] = (d["ct"] - d["ot"]).dt.total_seconds() / 3600
print(f"{len(d)} closed trades:", d["item"].value_counts().to_dict())

e = d[d["item"] == "eurusd"].sort_values("ot").copy()
e["burst"] = ((e["ot"].diff().dt.total_seconds() > 5) | (e["type"] != e["type"].shift())).cumsum()
b = e.groupby("burst").agg(
    ot=("ot", "min"), ot_max=("ot", "max"), side=("type", "first"), n=("size", "size"), lots=("size", "sum"),
    sizes=("size", lambda s: ",".join(sorted(set(map(str, s))))), px=("open_price", "mean"), pxmin=("open_price", "min"),
    pxmax=("open_price", "max"), ct_min=("ct", "min"), ct_max=("ct", "max"), cpx=("close_price", "mean"),
    pips=("pips", "mean"), pmin=("pips", "min"), net=("net", "sum"), swap=("swap", "sum"), hold=("hold_h", "median"))
b.to_csv(out)
print(f"{len(b)} EURUSD bursts ({(b.side == 'sell').sum()} sells, {(b.side == 'buy').sum()} buys) -> {out}")
