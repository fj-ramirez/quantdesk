# Continuation initiative

**Problem (user, 2026-09-09):** the assets they trade (SPX, SPY, QQQ, GLD, DIA via CFDs) are
fading breakouts and sitting in ranges. They want tools that show *which* markets currently
have continuation, and where money is rotating between sectors and industries.

**Framing.** Fade-versus-continue is, for optioned instruments, largely the dealer gamma
regime the engine already measures: long-gamma dealers absorb breakouts. Continuation tends to
live where dealer gamma is negative, thin or absent. So the initiative has two halves:

1. **Cross-asset scanning on daily bars** over a universe much wider than the five option
   underlyings (breakout ledger, trend/chop scorer, rotation, flows, cross-asset strip).
2. **Extending the existing GEX engine** to sector and industry ETFs and deriving a per-symbol
   regime verdict from wall spacing, flip distance and gamma composition (regime board).

Everything stays analysis-only. No order routing, no broker write APIs, data spend under
$50/month. All new data sources here are free and keyless by design.

## Dependency graph

```
T42 foundation: daily bars provider + table + universe + job + backfill      (Opus)
 ├── T43 breakout ledger, pure module + API   (Sonnet) ── T44 /scan page (Sonnet)
 ├── T45 trend/chop scorer, pure module + API (Opus)   ── T46 scan page columns (Sonnet)
 ├── T50 rotation math + API (Opus, needs T45 for nothing; needs T42) ── T51 /rotation page (Sonnet)
 ├── T52 ETF shares-outstanding ingest (Sonnet) ── T53 /flows page (Sonnet)
 └── T54 cross-asset regime strip (Sonnet; adds a Cboe index-history bar provider)

T47 extend option capture to sector/industry ETFs (Sonnet, independent of T42)
 └── T48 regime metrics, pure module + API (Opus; also needs T42 and T45 for IV/RV) ── T49 /regime page (Sonnet)
```

## Dispatch order

One Opus at a time; Sonnets may run alongside.

| Wave | Opus | Sonnet (parallel) | Note |
|---|---|---|---|
| 1 | T42 | T47 | T47 touches `models/chain.py`, `config.py`, `jobs/scheduler.py`; T42 touches `config.py` and `scheduler.py` too. **Supervisor writes the two new `Settings` fields and the two scheduler registration stubs first** to pre-empt the collision. |
| 2 | T45 | T43, T52 | All three read only T42's public interface. |
| 3 | T48 | T44, T46, T54 | T46 needs T45 merged. |
| 4 | T50 | T49, T53 | |
| 5 | | T51 | |

## Open decision for the user

**Universe.** The default universe in T42 is ETFs only (index, 11 sectors, ~10 industries,
commodities, rates, FX, international). Continuation is more likely in single names and in
the broker's full CFD list. The universe is a config string, so widening it later is free, but
Stooq's daily request budget (unknown until T42 verifies it) is the constraint on size.

## Where results go

`docs/validation.md` covers the GEX engine. Scan analytics get their own
`docs/validation-scan.md`, started by T43 and appended by T45, T48 and T50, holding the
hand-checked fixtures and every place the implementation deviates from a textbook definition.
