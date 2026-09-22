# State review — 2026-09-21

Known gaps and the prioritized fix list, as of the 2026-09-21 post-close session.

**Basis:** live Postgres via the read-only MCP connector, during a `/market-research` session on
QQQ. Nothing here was found by reading code — every finding came from the data, and every one
carries a query that reproduces it. That cuts both ways: the diagnoses are grounded in observed
rows, but the *causes* are inferred and none has been confirmed against the source.

Companion to [state-review-2026-09-05.md](state-review-2026-09-05.md); that document's list is
not re-litigated here.

---

## F1 — `FADE_PUT_WALL` enters at the **call wall** whenever spot trades above it

**Severity: high.** Wrong level, wrong side of the book, confidently wrong prose.

Five of the nineteen `FADE_PUT_WALL` decisions ever emitted use the call wall as the entry:

| id | sym | date | entry | spot | call_wall | put_wall |
|---|---|---|---|---|---|---|
| 78 | QQQ | 09-21 | **740** | 741.0 | 740 | 700 |
| 77 | SPY | 09-21 | **772** | 773.2 | 772 | 745 |
| 80 | DIA | 09-21 | **516** | 519.9 | 516 | 510 |
| 50 | QQQ | 09-18 | **720** | 722.0 | 720 | 700 |
| 32 | XLE | 09-11 | **65** | 65.12 | 65 | 62 |

**The signature is exact: every mislabel is a case where `spot > call_wall`, and every
correctly-resolved case has `spot < call_wall`.** Perfect separation across all 19 rows — GLD,
SLV, TLT, SMH, XLV and XLE-on-09-10 all resolve their put walls correctly, and in all of those
spot sits below the call wall.

Inferred cause: the wall selection picks *the nearest significant strike below spot* and labels
it the put wall, instead of selecting by gamma sign. When price trades through the call wall,
that strike becomes the nearest level below spot and gets grabbed. For QQQ id 78 the chosen
strike carries **+662mn net gamma** — it is the most positive strike in the entire book, and the
engine called it the put wall.

Repro:

```sql
SELECT d.id, d.underlying, d.entry, d.spot, l.call_wall, l.put_wall
FROM gex.decisions d
JOIN gex.gex_levels l ON l.snapshot_id = d.snapshot_id AND l.filter = d.filter
WHERE d.key = 'FADE_PUT_WALL' AND d.entry = l.call_wall;
```

**Fix:** a put wall must be the most-negative net-gamma strike, unconditionally — never a
positive-gamma strike, regardless of position relative to spot. Look in
`backend/app/modules/gex/scan/decisions.py` (T60) for wherever the level is chosen; it should
read `gex_levels.put_wall` (or recompute the argmin over `net_gex`), not scan for proximity.

**Add a guard and a test:** assert `sign(net_gex at chosen strike) < 0` for `FADE_PUT_WALL` and
`> 0` for `FADE_CALL_WALL`, and refuse to emit otherwise. The five rows above are a ready-made
regression fixture.

**Worth considering separately:** spot closing above the call wall is a real and distinct regime
— the cap has become a floor — and it may deserve its own key rather than being folded into
`FADE_PUT_WALL` by accident. That is a product decision, not part of the bug fix.

---

## F2 — The thesis text interpolates the wrong level into confident prose

**Severity: high.** Same root cause as F1, but a separate surface and worth its own fix.

`decisions.payload.thesis` for id 78 reads verbatim:

> "Dealers are long gamma (25% of gross): hedging sells strength and buys weakness, so a move
> into **the put wall at 740.00** meets demand."

This is the string a human or an LLM reads. It asserts a structural fact that the same database
contradicts one table over. Whatever templates this should take the wall's identity from the same
validated object the entry price comes from, so a fix to F1 cannot leave the prose behind.

---

## F3 — `net_gex = 0` recorded where nothing was measurable

**Severity: medium.** Violates "a null is never a zero" and sits adjacent to invariant 3.

Two snapshots on 09-21, both the session's first capture, both with full chains, both producing
**zero** rows in `gex_by_strike` — yet `gex_levels.net_gex` was written as `0` for all three
filters, with every level column null:

| id | sym | captured_at | contract_count | strike_rows | net_gex |
|---|---|---|---|---|---|
| 206 | QQQ | 09-21 13:43:03Z | 10,560 | 0 | **0** |
| 204 | SPX | 09-21 13:44:39Z | 29,518 | 0 | **0** |

Only these two were affected because the other 26 symbols' first captures of the day landed later
in the session.

Inferred cause: at 09:43 ET the provider has not yet published prior-session open interest, so
every contract has `OI = None` → correctly excluded per invariant 3 → but the aggregate then sums
an empty set to `0.0` instead of `NULL`. The exclusion logic is right; the aggregation is what
conflates *unmeasured* with *flat*.

A consumer reading `net_gex = 0` sees "dealers are gamma-flat," which is a strong and entirely
fabricated claim. The null level columns are the only hint, and nothing forces a reader to check
them.

Repro:

```sql
SELECT s.id, s.underlying, s.captured_at, s.contract_count,
       count(k.id) AS strike_rows,
       max(CASE WHEN l.filter = 'ALL' THEN l.net_gex END) AS all_net_gex
FROM gex.snapshots s
JOIN gex.gex_levels l ON l.snapshot_id = s.id
LEFT JOIN gex.gex_by_strike k ON k.snapshot_id = s.id
GROUP BY s.id, s.underlying, s.captured_at, s.contract_count
HAVING count(k.id) = 0;
```

**Fix:** empty aggregate → `NULL`, not `0`. Two follow-ups to decide on:

- Should a capture that admits zero contracts be persisted as a snapshot at all, or
  rejected/flagged?
- `ZERO_DTE` rows at EOD show the same `net_gex = 0` + null-levels shape. There it is arguably
  legitimate (no 0DTE contracts exist), but it is indistinguishable from the F3 case. Worth
  separating "no contracts in this bucket" from "contracts existed but none were usable."

---

## F4 — Five-session capture outage across the entire universe, 09-14 → 09-18

**Severity: high.** This is September quarterly opex week, and it is simply missing.

| Date | Symbols | Snapshots |
|---|---|---|
| 09-09 | 28 | 48 |
| 09-10 | 28 | 28 |
| 09-11 | 28 | 85 |
| **09-14 → 09-18** | **0** | **0** |
| 09-19 (Sat) | 3 | 3 |
| 09-20 (Sun) | 25 | 25 |
| 09-21 | 28 | 144 |

Not `catchup_skipped` — those were five open sessions, and the whole 28-symbol universe is
absent. Recovery was partial and staggered (3 symbols Saturday, 25 Sunday, 28 Monday), which
suggests the worker came back on its own rather than being restarted deliberately.

Cost: the QQQ gamma regime flipped from **−2.17bn (09-11)** to **+4.80bn (09-21)** entirely
inside the gap, across the quarterly. Both endpoints exist; the path does not. That is the single
most valuable week of the quarter to have lost.

Repro:

```sql
SELECT captured_at::date AS d, count(DISTINCT underlying) AS symbols, count(*) AS snaps
FROM gex.snapshots WHERE captured_at >= '2026-09-05'
GROUP BY 1 ORDER BY 1;
```

**Wanted:**

1. Root cause from the `gex-capture` container logs for 09-12 → 09-19.
2. Whether `jobs/catchup.py` should have backfilled and did not — and if backfill is impossible
   for options chains (it is, for live OI), say so explicitly in the docs so nobody expects
   recovery.
3. An actual alert. `GET /api/gex/health/capture` exists and evidently nothing watches it. A
   five-day silent outage is the failure mode that endpoint was built for.

---

## F5 — No expiry dimension in stored GEX

**Severity: high for the product.** Not a bug; a capability gap.

`gex.gex_by_strike` is `(id, snapshot_id, filter, strike, call_gex, put_gex, net_gex)`. The only
expiry slicing anywhere in Postgres is the `ZERO_DTE` / `EX_ZERO_DTE` filter.

Consequence: for any question with a horizon — "what happens this week" — you cannot tell how much
of a wall expires Friday versus October versus January. On 09-21 the QQQ 740 wall was +662mn and
the desk could not say what fraction of it survives past that Friday. That is the first thing
anyone asks about a wall.

Per-contract rows live only in Parquet by design (invariant 5), so the fix is a rollup, not a
schema violation: either a `gex_by_expiry` table (or an `expiry` column on the strike rollup)
written at capture time, or an API route that resolves the snapshot's Parquet on demand via
`storage.parquet.resolve_snapshot_path`. The capture job already has the per-contract frame in
hand — this is cheap at write time and expensive later.

---

## F6 — No implied-volatility series anywhere

**Severity: medium.** A whole half of options analysis is absent.

The decision engine says so about itself, in `payload.structure`:

> "…no implied-versus-realized view is available."

The only vol series in the database are `vol.vix`, `vol.vix9d`, `vol.vix3m`, `vol.vix6m` and
`vol.skew` — all SPX-referenced, living in `terminal`. There is no VXN, and no per-underlying IV,
**even though the captured chains carry IV and the engine already consumes it for Greeks.** So for
QQQ (realizing 19.0% on 10 days against SPY's 12.8%) there is no way to say whether premium is
rich or cheap.

**Suggestion:** persist ATM IV and a 30-day constant-maturity IV per snapshot per underlying. The
input is already in memory at capture; this is a few columns on `gex.snapshots` or a small sibling
table, and it unlocks every rich/cheap question the desk currently cannot answer.

---

## F7 — Documentation drift

Each item causes a wrong read by an agent that trusts the docs over the data.

**a. `cmdty.gold` now has data.** `universe.py` marks it `_pending`, and both `CLAUDE.md` and the
market-research skill state the desk has no gold price. The board returns **398.4** for 09-21
(`derived_lag`, GLD proxy) and `ust.10y.real → cmdty.gold` computes (β −0.120, t −3.47,
significant). Update both — and describe it accurately as an **ETF proxy**, not a bullion fix,
since that distinction is the reason the original caveat existed.

**b. `ust_cc.*` is fresher than `ust.*`, inverting the documented lag.** On 09-21 `ust_cc.10y` is
09-21 while `ust.10y.nominal` is 09-18. If the board's stale-flagging is keyed on the documented
assumption that `ust_cc.*` lags, it is now flagging the fresh series and passing the stale one.

**c. `vol.vix` is stale while its siblings are not.** `vol.vix` last prints 09-18 (14.81) while
`vol.vix9d` / `vix3m` / `vix6m` are all 09-21. The derived ratios correctly inherit the 09-18
vintage, so the board shows a 09-21 VIX3M beside a 09-18 ratio — internally consistent but
confusing, and it means the headline VIX is three days old. Worth checking that one series'
ingest path.

**d. The market-research skill hardcodes a stale track record.** It states "As of 2026-09-20 (32
resolved) … Overall +0.27R." Actual on 09-21: **40 resolved, +0.187R, SE ±0.180**, with
`FADE_CALL_WALL` +1.16R (4/7), `FADE_PUT_WALL` +0.13R (1/4), `CONTINUATION_DOWN` +0.09R (8/26),
`CONTINUATION_UP` −1.21R (0/3). Numbers baked into a prompt file rot silently and are then quoted
with full confidence. Either have the skill run the query instead of quoting results, or have a
job regenerate that block.

---

## F8 — `is_eod` on a weekend capture is ambiguous

**Severity: low.**

Snapshot 178 is `2026-09-20 15:10:08Z` — a Sunday — with `is_eod = true` and 10,436 contracts,
holding Friday's post-opex book. The flag is technically defensible (it is an end-of-session book)
but a consumer filtering `WHERE is_eod` and grouping by `captured_at::date` gets a phantom Sunday
session. That is exactly what the first pass at the freshness query in this session did.

**Suggestion:** record the **session date the chain belongs to** as a column distinct from
`captured_at`. That makes the weekend-capture rule enforceable in SQL instead of documented in
prose, and it is the same column F5's expiry work will want anyway.

---

## Suggested order

1. **F1 + F2** — actively emitting wrong levels with confident justification. Smallest fix,
   largest correctness gain, regression fixture already exists.
2. **F4** — alerting, before the next outage. Root-causing the old one is secondary to not
   repeating it silently.
3. **F3** — small, and it is an invariant violation.
4. **F5** — the biggest capability gap; do it at capture time or pay more for it later.
5. **F7** — cheap, and each item is an active source of wrong agent reads.
6. **F6**, then **F8**.

---

## Not checked

Two things were out of reach from a read-only seat, both bearing on F4:

- the `gex-capture` container logs for the outage window;
- whether `jobs/catchup.py` attempted anything during or after it.

Both need shell access on the homeserver.
