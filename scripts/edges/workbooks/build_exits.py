import sys
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.formula import ArrayFormula

data = Path(sys.argv[1])
out = sys.argv[2]

VARIANTS = [("Pullback trail (row 53)", "pullback-", "Candle stop, row 53's pullback trail (baseline, #548)"),
            ("Breakeven 1R", "be1-", "Candle stop, pullback trail + breakeven at +1R (row 56, #560)"),
            ("ATR3 trail", "atr3-", "Candle stop, chandelier: highest high − 3 × ATR(14) of 4h (row 56, #562)"),
            ("Breakeven + ATR3", "be1atr3-", "Candle stop, breakeven at +1R then the ATR3 chandelier (row 56, #564)")]
SYMS = [("S&P.fs", "s-p.fs"), ("NAS100.fs", "nas100.fs")]

F = "Arial"
base, bold = Font(name=F, size=10), Font(name=F, size=10, bold=True)
blue = Font(name=F, size=10, color="0000FF")
green = Font(name=F, size=10, color="008000")
head_fill = PatternFill("solid", fgColor="D9E1F2")
title = Font(name=F, size=12, bold=True)
thin = Border(bottom=Side(style="thin", color="808080"))

wb = Workbook()
summ = wb.active
summ.title = "Summary"

# -- Assumptions --------------------------------------------------------------------------
asm = wb.create_sheet("Assumptions")
asm["A1"] = "Assumptions"
asm["A1"].font = title
asm.append([])
asm.append(["Symbol", "$ per 1.00 index point, 0.01 lot", "Source"])
for c in asm[3]:
    c.font, c.fill = bold, head_fill
asm.append(["S&P.fs", 0.50, "broker.symbol_specs contract size (scripts/edges/_common.py _usd_per_point)"])
asm.append(["NAS100.fs", 0.20, "broker.symbol_specs contract size (scripts/edges/_common.py _usd_per_point)"])
for r in (4, 5):
    asm.cell(r, 1).font = base
    asm.cell(r, 2).font = blue
    asm.cell(r, 2).number_format = "$0.00"
    asm.cell(r, 3).font = base
notes = [
    "",
    "Target (R)",
    "",
    "Notes",
    "TP0.5R sheets: take-profit at entry + 0.5 × initial risk (backlog row 55). B7 is the value the backtest used; "
    "changing it moves only the Target price column, not the simulated trades.",
    "Prices are broker M1 (Axi), 2019-07 → 2026-10-09. Entry = open of the first M1 bar after the signal 4h close.",
    "Times are New York wall clock (4h candles anchored at 17:00 NY = Axi server midnight) and UTC.",
    "Cost = 2 × the spread at entry (EdgeLab's doubled-cost gate), in index points.",
    "Roll = the .fs roll step removed when a trade spans a contract roll (broker.rolls), in index points.",
    "Initial risk = entry − initial stop. R = net points ÷ initial risk. Swap on .fs CFDs is 0.",
    "MAE / MFE = worst / best open excursion from entry to exit on M1 bid bars, in points; ÷ initial risk for R.",
    "Exit reason: stop = M1 low touched the stop; ema20 = 4h close below EMA20; gap = filled at or below the stop.",
    "Blue cells are inputs from the backtest; black cells are formulas. Script: scripts/edges/2026-10-16-53-4h-desk-trend.py",
]
for n in notes:
    asm.append([n])
asm["B7"] = 0.5
asm["B7"].font = blue
asm["B7"].number_format = "0.0\"R\""
asm["C7"] = "From the user, 2026-10-16 (\"0.5:1 ratio\")"
asm["C7"].font = base
asm.cell(7, 1).font = bold
asm.cell(9, 1).font = bold
for r in range(10, 6 + len(notes)):
    asm.cell(r, 1).font = base
asm.column_dimensions["A"].width = 14
asm.column_dimensions["B"].width = 30
asm.column_dimensions["C"].width = 70

HEAD = ["#", "Symbol", "Entry (New York)", "Exit (New York)", "Entry (UTC)", "Exit (UTC)", "Hold (hours)",
        "Entry price", "Initial stop", "Exit price", "Exit reason", "Spread at entry (pts)", "Roll step (pts)",
        "Gross (pts)", "Cost 2× spread (pts)", "Net (pts)", "Initial risk (pts)", "R", "Net % of entry",
        "$ per 0.01 lot", "MAE (pts)", "MFE (pts)", "MAE time (New York)", "MFE time (New York)", "MAE (R)",
        "MFE (R)", "Gross ÷ MFE", "Year", "MAE ($ at 0.01 lot)", "MFE ($ at 0.01 lot)", "Target price"]
WIDTH = [6, 11, 18, 18, 18, 18, 10, 12, 12, 12, 11, 12, 11, 11, 12, 11, 12, 9, 11, 11, 10, 10, 18, 18, 9, 9, 10, 7,
         12, 12, 12]

def ny(x):
    return x.tz_convert("America/New_York").tz_localize(None).to_pydatetime()


sheets = []
for name, pre, desc in VARIANTS:
    frames = []
    for sym, f in SYMS:
        d = pd.read_csv(data / f"4h-desk-exits-{pre}{f}.csv")
        d["symbol"] = sym
        frames.append(d)
    d = pd.concat(frames)
    d["t0"] = pd.to_datetime(d["t0"], utc=True)
    d["t1"] = pd.to_datetime(d["t1"], utc=True)
    d["mae_ts"] = pd.to_datetime(d["mae_ts"], utc=True)
    d["mfe_ts"] = pd.to_datetime(d["mfe_ts"], utc=True)
    d = d.sort_values(["t0", "symbol"]).reset_index(drop=True)
    ws = wb.create_sheet(name)
    ws["A1"] = f"4h desk strategy — {desc}"
    ws["A1"].font = title
    ws["A2"] = "Long only. Entry: 4h close > EMA20, EMA20 > EMA50, MACD(8,17) > 0. Initial stop: signal-candle low − 1 spread. Exit: stop or 4h close < EMA20."
    ws["A2"].font = base
    hr = 4
    for j, h in enumerate(HEAD, 1):
        c = ws.cell(hr, j, h)
        c.font, c.fill, c.border = bold, head_fill, thin
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, t in enumerate(d.itertuples(), start=hr + 1):
        ny0 = t.t0.tz_convert("America/New_York").tz_localize(None).to_pydatetime()
        ny1 = t.t1.tz_convert("America/New_York").tz_localize(None).to_pydatetime()
        vals = [i - hr, t.symbol, ny0, ny1, t.t0.tz_localize(None).to_pydatetime(),
                t.t1.tz_localize(None).to_pydatetime(),
                f"=(D{i}-C{i})*24", t.entry, t.init_stop, t.exit, t.reason, t.spread, t.roll,
                f"=J{i}-H{i}-M{i}", f"=2*L{i}", f"=N{i}-O{i}", f"=H{i}-I{i}", f"=P{i}/Q{i}", f"=P{i}/H{i}",
                f"=P{i}*INDEX(Assumptions!$B$4:$B$5,MATCH(B{i},Assumptions!$A$4:$A$5,0))",
                t.mae, t.mfe, ny(t.mae_ts), ny(t.mfe_ts), f"=U{i}/Q{i}", f"=V{i}/Q{i}",
                f'=IF(V{i}>0,N{i}/V{i},"")', f"=YEAR(C{i})",
                f"=-U{i}*INDEX(Assumptions!$B$4:$B$5,MATCH(B{i},Assumptions!$A$4:$A$5,0))",
                f"=V{i}*INDEX(Assumptions!$B$4:$B$5,MATCH(B{i},Assumptions!$A$4:$A$5,0))",
                "none"]
        for j, v in enumerate(vals, 1):
            c = ws.cell(i, j, v)
            c.font = blue if j in (2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 21, 22, 23, 24) else (green if j == 20 else base)
        if t.reason == "gap":  # filled at or below the stop: no initial risk, so no R
            for j in (18, 25, 26):
                ws.cell(i, j).value = ""
        for j in (3, 4, 5, 6, 23, 24):
            ws.cell(i, j).number_format = "yyyy-mm-dd hh:mm"
        ws.cell(i, 7).number_format = "0.0"
        for j in (8, 9, 10, 12, 13, 14, 15, 16, 17):
            ws.cell(i, j).number_format = "#,##0.00;(#,##0.00);-"
        ws.cell(i, 18).number_format = "0.00;(0.00);-"
        ws.cell(i, 19).number_format = "0.000%;(0.000%);-"
        ws.cell(i, 20).number_format = "$#,##0.00;($#,##0.00);-"
        ws.cell(i, 21).number_format = ws.cell(i, 22).number_format = "#,##0.00;(#,##0.00);-"
        ws.cell(i, 25).number_format = ws.cell(i, 26).number_format = "0.00;(0.00);-"
        ws.cell(i, 27).number_format = "0%;(0%);-"
        ws.cell(i, 28).number_format = "0"
        ws.cell(i, 29).number_format = ws.cell(i, 30).number_format = "$#,##0.00;($#,##0.00);-"
        ws.cell(i, 31).number_format = "#,##0.00"
        ws.cell(i, 29).font = ws.cell(i, 30).font = ws.cell(i, 31).font = green
    last = hr + len(d)
    for j, w in enumerate(WIDTH, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = ws.cell(hr + 1, 3)
    ws.auto_filter.ref = f"A{hr}:{get_column_letter(len(HEAD))}{last}"
    ws.cell(hr, 18).comment = Comment("Net points ÷ (entry − initial stop). Not comparable across stop variants: "
                                      "the denominator changes with the stop.", "Claude")
    ws.cell(hr, 21).comment = Comment("Maximum adverse excursion: entry − lowest M1 low from the entry bar up to the "
                                      "exit (the exit price included), roll steps removed. Bid prices.", "Claude")
    ws.cell(hr, 22).comment = Comment("Maximum favorable excursion: highest M1 high from the entry bar up to the exit "
                                      "(the exit price included) − entry, roll steps removed. Bid prices.", "Claude")
    ws.cell(hr, 27).comment = Comment("Share of the best open profit the trade kept at exit, before costs.", "Claude")
    sheets.append((name, desc, hr + 1, last))

# -- Summary --------------------------------------------------------------------------------
summ["A1"] = "4h desk strategy backtest — every S&P.fs and NAS100.fs trade"
summ["A1"].font = title
summ["A2"] = ("Row 56: exits that keep open profit, candle stop (ledger #560–565). Per-trade figures below; verdicts "
              "and the random-entry control are on the 'Random control' sheet — see docs/edges/4h-desk-trend.md.")
summ["A2"].font = base
cols = ["Stop variant", "Symbol", "Trades", "Win rate", "Mean R", "Median R", "Mean net % per trade",
        "Total net pts", "Total $ at 0.01 lot", "Stop exits", "EMA20 exits", "Avg hold (hours)",
        "Mean MAE (R)", "Median MAE (R)", "Mean MFE (R)", "Median MFE (R)", "Share MFE ≥ 1R",
        "Mean MAE (pts)", "Mean MAE ($ at 0.01 lot)", "Worst MAE ($ at 0.01 lot)", "Target exits"]
r0 = 4
for j, h in enumerate(cols, 1):
    c = summ.cell(r0, j, h)
    c.font, c.fill, c.border = bold, head_fill, thin
    c.alignment = Alignment(wrap_text=True, vertical="center")
r = r0 + 1
for name, desc, a, b in sheets:
    q = f"'{name}'!"
    for sym, _ in SYMS:
        B, R, P, T, K, G, S, Y, Z, U = (f"{q}${x}${a}:${x}${b}" for x in "BRPTKGSYZU")
        AC = f"{q}$AC${a}:$AC${b}"
        row = [name, sym, f'=COUNTIFS({B},B{r})', f'=COUNTIFS({B},B{r},{R},">0")/C{r}',
               f"=AVERAGEIFS({R},{B},B{r})", f"=MEDIAN(IF({B}=B{r},{R}))",
               f"=AVERAGEIFS({S},{B},B{r})", f"=SUMIFS({P},{B},B{r})", f"=SUMIFS({T},{B},B{r})",
               f'=COUNTIFS({B},B{r},{K},"stop")', f'=COUNTIFS({B},B{r},{K},"ema20")',
               f"=AVERAGEIFS({G},{B},B{r})", f"=AVERAGEIFS({Y},{B},B{r})", None,
               f"=AVERAGEIFS({Z},{B},B{r})", None, f'=COUNTIFS({B},B{r},{Z},">=1")/C{r}',
               f"=AVERAGEIFS({U},{B},B{r})", f"=AVERAGEIFS({AC},{B},B{r})", None,
               f'=COUNTIFS({B},B{r},{K},"target")']
        for j, v in enumerate(row, 1):
            summ.cell(r, j, v).font = green if j > 2 else base
        summ.cell(r, 6).value = None  # median filled below as an array formula
        summ.cell(r, 6).value = ArrayFormula(f"F{r}", f"=MEDIAN(IF({B}=B{r},{R}))")
        summ.cell(r, 6).font = green
        summ.cell(r, 4).number_format = "0.0%"
        summ.cell(r, 5).number_format = summ.cell(r, 6).number_format = "0.00;(0.00);-"
        summ.cell(r, 7).number_format = "0.000%;(0.000%);-"
        summ.cell(r, 8).number_format = "#,##0.0;(#,##0.0);-"
        summ.cell(r, 9).number_format = "$#,##0.00;($#,##0.00);-"
        summ.cell(r, 12).number_format = "0.0"
        summ.cell(r, 14).value = ArrayFormula(f"N{r}", f"=MEDIAN(IF({B}=B{r},{Y}))")
        summ.cell(r, 16).value = ArrayFormula(f"P{r}", f"=MEDIAN(IF({B}=B{r},{Z}))")
        for j in (13, 14, 15, 16):
            summ.cell(r, j).number_format = "0.00;(0.00);-"
            summ.cell(r, j).font = green
        summ.cell(r, 17).number_format = "0.0%"
        summ.cell(r, 20).value = ArrayFormula(f"T{r}", f"=MIN(IF({B}=B{r},{AC}))")
        summ.cell(r, 18).number_format = "#,##0.0"
        for j in (19, 20):
            summ.cell(r, j).number_format = "$#,##0.00;($#,##0.00);-"
        for j in (18, 19, 20, 21):
            summ.cell(r, j).font = green
        r += 1
for j, w in enumerate([20, 11, 8, 9, 9, 9, 12, 12, 13, 9, 9, 10, 9, 9, 9, 9, 9, 10, 12, 12, 9], 1):
    summ.column_dimensions[get_column_letter(j)].width = w
summ.freeze_panes = "A5"

import json  # noqa: E402
J = json.load(open(data / "4h-desk-exits.json"))
rc = wb.create_sheet("Random control", 1)
rc["A1"] = "Random-entry control: the same stop and exits on 200 random entry draws per cell"
rc["A1"].font = title
rc["A2"] = ("Values from scripts/edges/2026-10-16-56-4h-desk-exits.py (data/broker-export/4h-desk-exits.json), "
            "not formulas: the random draws are not in this workbook. Pass needs the signal above the 95th percentile.")
rc["A2"].font = base
rh = ["Exit", "Symbol", "Signal trades", "Random trades (median)", "Signal mean net %/trade",
      "Random p50", "Random p95", "Signal percentile", "Signal total %", "Random total p50", "Random total p95",
      "Daily t", "Sharpe", "Buy-and-hold Sharpe", "Verdict"]
for j, h in enumerate(rh, 1):
    c = rc.cell(4, j, h)
    c.font, c.fill, c.border = bold, head_fill, thin
    c.alignment = Alignment(wrap_text=True, vertical="center")
rr = 5
for name, pre, _ in VARIANTS:
    for sym, _f in SYMS:
        v = J[f"{sym}|{pre[:-1]}"]
        row = [name, sym, v["trades"], v["rand_trades_median"], v["mean_pct"] / 100, v["rand_mean_pct_p50"] / 100,
               v["rand_mean_pct_p95"] / 100, v["signal_pctile"] / 100, v["total"] / 100, v["rand_total_p50"] / 100,
               v["rand_total_p95"] / 100, v["t"], v["sharpe"], v["bh_sharpe"], v["verdict"]]
        for j, x in enumerate(row, 1):
            c = rc.cell(rr, j, x)
            c.font = blue
        for j in (5, 6, 7):
            rc.cell(rr, j).number_format = "0.000%;(0.000%);-"
        for j in (8, 9, 10, 11):
            rc.cell(rr, j).number_format = "0.0%;(0.0%);-"
        for j in (12, 13, 14):
            rc.cell(rr, j).number_format = "0.00;(0.00);-"
        rr += 1
for j, w in enumerate([24, 11, 9, 10, 11, 10, 10, 10, 10, 10, 10, 8, 8, 10, 8], 1):
    rc.column_dimensions[get_column_letter(j)].width = w
rc.freeze_panes = "A5"
for ref in ("A7", "B7", "C7", "A10"):  # no target in these exits
    asm[ref] = None
wb.save(out)
print("rows:", [(n, b - a + 1) for n, _, a, b in sheets])
