# Normalized chain schema

The data contract shared by every provider, the storage layer, the Greeks module and the GEX
engine. Defined in [`backend/app/models/chain.py`](../backend/app/models/chain.py); the
provider interface is in [`backend/app/providers/base.py`](../backend/app/providers/base.py).

Field-level documentation lives in the module docstrings. This page is the reference for
**units and conventions** — the things a later task can get wrong without any test failing.

---

## The five conventions that matter

1. **IV is a decimal fraction.** `iv = 0.18` means 18 % annualized. Never a percent.
2. **Greeks carry no dealer sign.** They are stored as reported for a long holder of one
   contract. `gamma >= 0` for calls *and* puts; `delta > 0` for calls, `< 0` for puts. The
   ±1 dealer sign is the GEX engine's job (T08), applied exactly once.
3. **All datetimes are tz-aware and stored in UTC.** Naive datetimes are rejected, not
   assumed.
4. **Missing vendor data is `None`, never `0`.** `open_interest = 0` means zero open
   interest; `open_interest = None` means unknown, and the contract must be excluded from
   aggregates rather than counted as zero.
5. **`root` and `underlying` are both required and are not redundant.** SPX and SPXW merge
   into the `SPX` underlying for aggregation, but the natural key of a contract is
   `(underlying, root, expiry, strike, right)`.

---

## `OptionContract`

Frozen pydantic model, `extra="forbid"`. Everything above the rule is always present and is
derived from the OCC symbol; everything below it is `None` when the vendor did not report it.

| Field | Type | Unit / meaning |
|---|---|---|
| `occ_symbol` | `str` | OCC symbol, uppercase, root **not** space-padded, e.g. `SPXW260904P07700000`. |
| `root` | `str`, 1–6 chars | Vendor root exactly as listed: `SPX`, `SPXW`, `SPY`, `QQQ`. |
| `underlying` | `Underlying` enum | Canonical aggregation key: `SPX`, `SPY`, `QQQ`. `SPXW` → `SPX`. |
| `expiry` | `date` | Expiry **date**. The expiry *time* comes from `settlement`. |
| `settlement` | `Settlement` enum | `AM` (09:30 America/New_York) or `PM` (16:00 America/New_York). Root-derived. |
| `strike` | `float`, > 0 | Dollars for SPY/QQQ, index points for SPX. Exactly decoded from the OCC integer. |
| `right` | `Right` enum | `C` or `P`. |
| | | |
| `bid` | `float \| None`, ≥ 0 | Best bid, dollars **per unit of underlying** (multiply by `multiplier` for the contract's cash price). |
| `ask` | `float \| None`, ≥ 0 | Best ask, same units as `bid`. |
| `last` | `float \| None`, ≥ 0 | Last trade price, same units as `bid`. |
| `volume` | `int \| None`, ≥ 0 | Contracts traded in the current session. |
| `open_interest` | `int \| None`, ≥ 0 | Open contracts. Published once daily by OCC, overnight — never intraday (PLAN.md §1). |
| `iv` | `float \| None`, > 0 | **Decimal fraction**, annualized. `0.1064` = 10.64 %. |
| `delta` | `float \| None` | ∂price/∂spot per unit. Long-holder sign: positive calls, negative puts. |
| `gamma` | `float \| None`, ≥ 0 | ∂delta/∂spot per **1.00** move in the underlying, per unit. Not per 1 %, not per contract. |
| `vega` | `float \| None`, ≥ 0 | Vendor-defined; Cboe reports per 1 volatility *point*. Display only — T07 computes its own. |
| `theta` | `float \| None` | Vendor-defined; Cboe reports per calendar day, per unit. Usually negative. Display only. |
| `multiplier` | `int`, > 0, default `100` | Units of underlying per contract. **Read this field; do not hardcode 100.** |
| `last_trade_time` | `datetime \| None` | Tz-aware, normalized to UTC. |

`OptionContract.from_occ(occ_symbol, **market_data)` is the intended constructor for
providers: it derives `root`, `underlying`, `expiry`, `settlement`, `strike` and `right` from
the symbol so that every provider produces identical identity fields.

### Why `strike` is a `float`, not a `Decimal`

The OCC symbol encodes the strike as an 8-digit integer number of thousandths of a dollar.
For every strike in scope (up to 100,000,000 thousandths, far below 2⁵³) the map
`int → int/1000 → float` is deterministic and injective, and `round(strike * 1000)` recovers
the original integer exactly — `strike_to_occ_int()` does this, and the test suite verifies it
exhaustively over every $0.005 increment to $20,000 and every $0.01 increment to $1,000. So
grouping `by_strike` on floats is safe: two distinct OCC strikes can never collide on one
double.

`Decimal` would be marginally more principled but would force object-dtype columns in pandas,
`decimal128` in Parquet, and a conversion at the boundary of every NumPy call in T07/T08 — for
no accuracy gained. Use `strike_to_occ_int()` if you need the exact integer for a database
unique key.

---

## `ChainSnapshot`

Frozen. One snapshot = one underlying observed at one instant = one Parquet file (T04) = one
input to `compute_all` (T08).

| Field | Type | Unit / meaning |
|---|---|---|
| `underlying` | `Underlying` enum | `SPX`, `SPY` or `QQQ`. |
| `spot` | `float`, > 0 | Underlying price at `captured_at`. |
| `captured_at` | `datetime`, aware | **The vendor's own payload timestamp, in UTC** — not the time the HTTP call returned, but also *not reliably the time the data itself became effective* (see the T34 correction below). |
| `source` | `str` | Provider `name`, e.g. `"cboe"`. Stable forever, since captured data is indexed by it. |
| `delayed_minutes` | `int`, ≥ 0 | Vendor entitlement delay. `0` = real-time, `15` = Cboe delayed JSON. |
| `contracts` | `tuple[OptionContract, ...]` | Every contract the vendor listed. Accepts any sequence at construction. |

Helpers: `len(snapshot)`, `snapshot.expiries` (sorted distinct dates), `snapshot.roots`.

A snapshot whose contracts disagree with its `underlying` is rejected at construction.

### `captured_at` is not "effective time" — the T34 correction

An earlier version of this page (and of `ChainSnapshot`'s own docstring) documented
`captured_at` as "the effective time of the data". That was wrong, and the supervisor caught
it live: Cboe's top-level `timestamp` is **payload-generation time**, not data-effective time.
Measured 2026-09-04 at 17:55 ET — nearly two hours after the 16:00 close —
`timestamp` read `17:54:46 ET` and kept advancing on every request, while `data.current_price`
(and the whole quote chain) stayed frozen at the close.

`captured_at` still stores that raw vendor value verbatim, unchanged by this fix. That is
deliberate, not an oversight: every existing reader of `captured_at` — the duplicate-capture
check in `app.jobs.capture`, T29's per-day `is_eod` guard (`has_eod_snapshot_today`), and the
`levels/history` date-range query — only needs it to be NY-date-correct and roughly
monotonic, which the vendor timestamp is. Redefining the field to "the honest data time"
would have required migrating all three, for a field whose actual behavior (see T34's own
writeup in `TASKS.md`) makes the duplicate-capture check *already* near-dead: the timestamp
advances on essentially every call, so an exact `(underlying, captured_at)` match almost
never fires; T29's `is_eod`-per-day guard is what actually prevents double EOD captures.

Instead, the honest "as of" instant lives in a **new, purely derived field**:
`SnapshotMetaOut.effective_at` (`backend/app/api/schemas.py`), computed at read time by
`app.jobs.calendar.effective_data_time(captured_at, delayed_minutes)`:

- During a regular NY session (09:30–16:00 ET on a trading day), `effective_at ==
  captured_at` — a delayed vendor timestamp genuinely is the honest reading, which is the
  case T18's future intraday polling must keep getting.
- Outside a regular session (after the close, before the open, or on a weekend/holiday),
  `effective_at` clamps to the most recent 16:00 ET close plus `delayed_minutes` (16:15 ET for
  the free Cboe feed) — a fixed instant that does not keep advancing just because someone
  loads the dashboard later in the evening.

Nothing is stored on disk or in Postgres for this — it is recomputed on every read from
`captured_at` and `delayed_minutes`, so it costs nothing and can never drift from the row it
describes. The frontend's freshness badge (`TopBar`'s `DataFreshnessBadge`, `KeyLevels`)
reads `effective_at` directly rather than reimplementing any market-hours logic itself.

---

## Settlement rule

`settlement` is derived from the **root**, never from the expiry date:

| Root | Settlement | Settles against |
|---|---|---|
| `SPX` | `AM` | The SET opening print, 09:30 America/New_York on the expiry date |
| `SPXW` | `PM` | The 16:00 America/New_York close |
| `SPY`, `QQQ`, other equity/ETF roots | `PM` | The 16:00 America/New_York close |

Two facts from the live Cboe chain (2026-09-04) rule out the tempting "third Friday ⇒ AM"
shortcut:

- Third Fridays carry **both** series. On 2026-09-18, 2026-10-16, 2026-11-20, 2026-12-18 and
  2027-01-15, `SPX…C07710000` (AM) and `SPXW…C07710000` (PM) both exist, same strike, same
  right, same date, different settlement and different IV (0.1064 vs 0.1080).
- Not every AM expiry is a Friday: `SPX` is listed for **2027-06-17, a Thursday**.

T07 turns this into a time to expiry: AM expires at 09:30 NY, PM at 16:00 NY, on `expiry`.

## SPX / SPXW merge

PLAN.md §3 item 2 requires SPX and SPXW to merge. The mechanism is `underlying`: both roots
map to `Underlying.SPX`, and the engine groups by `underlying`, so per-strike and per-expiry
GEX sums both series — which is correct, since dealers are short gamma against both.

The trap is uniqueness. `(underlying, expiry, strike, right)` is **not** unique on third
Fridays, so any Parquet dedupe, database unique constraint or dictionary keyed that way will
silently destroy real contracts. Always include `root`.

## `parse_occ_symbol`

```python
parse_occ_symbol(s: str) -> OccSymbol  # NamedTuple (root, expiry, right, strike)
```

The official OCC form is 21 characters: a root left-justified and **space-padded to six**,
then `YYMMDD`, then `C`/`P`, then an 8-digit strike in thousandths. Vendor JSON generally
omits the padding (Cboe sends the 19-character `SPXW260904P07700000`). Both forms are
accepted, as are lowercase input and surrounding whitespace.

The 15-character tail is parsed by **fixed width from the right**, not by a greedy
left-anchored match, because adjusted-option roots may end in a digit (`SPY1`) and would
otherwise be ambiguous.

Two-digit years map to 2000–2099. Anything malformed raises `ValueError`: a symbol this
parser cannot read is a contract the application cannot price, not something to guess at.

## Provider interface

```python
class OptionChainProvider(ABC):
    name: str                                              # abstract property
    delayed_minutes: int                                   # abstract property
    async def fetch_chain(self, underlying: str) -> ChainSnapshot: ...
```

Errors: `ProviderError` → `UpstreamUnavailable` (transient, retryable — raise after the
provider's own retry budget is spent) and `SymbolNotSupported` (permanent). The T05 scheduler
catches `ProviderError` so a bad fetch cannot kill the job loop; an unwrapped `httpx`
exception defeats that.

Providers must **not** filter the chain. Return every contract the vendor lists; expiry and
strike filtering belongs to the engine. A provider that silently drops contracts makes net GEX
quietly wrong with no visible symptom.

---

## Notes for the Cboe adapter (T02)

Verified against the live endpoint on 2026-09-04 (28,650 SPX contracts, 12,456 SPY).

**IV is already decimal — do not divide by 100.** The original task brief said Cboe returns
percent-like IV. That is true of the *index-level* `data.iv30` (`11.276` = 11.276 %), which
this schema does not carry. The *per-contract* `iv` is already a decimal fraction: the ATM
`SPX260918C07710000` quotes `iv: 0.1064`, consistent with `iv30: 11.276`. Across the full SPX
chain, per-contract IV ranges 0.0555 – 8.3191 with a mean of 0.249. Dividing by 100 would
understate every gamma by orders of magnitude. A useful smoke test: median IV over the chain
must be below 1.0.

**Timezones are mixed within one payload.** The top-level `timestamp` (`"2026-09-04 18:05:33"`)
is naive **UTC**; every `last_trade_time` (`"2026-09-04T13:50:31"`) is naive
**America/New_York**. They differ by exactly the 4-hour EDT offset plus the 15-minute delay.
Attach `ZoneInfo("America/New_York")` to trade times and `UTC` to the snapshot timestamp — a
blanket assumption is wrong for one of the two.

**Sentinel zeros.** Cboe emits `iv: 0.0` for illiquid contracts it cannot invert (897 of
28,650 SPX contracts). Map these to `None`; the model rejects a non-positive IV so this cannot
slip through. `gamma: 0.0` (7,449 contracts) is *not* a sentinel — it is a genuine value
rounded to four decimals, which is one reason PLAN.md §2 has T07 recompute Greeks locally
rather than trusting vendor gamma. `last_trade_time` is `null` on 6,804 contracts.

**Extreme IVs are real.** Deep-ITM contracts carry vendor IVs up to 8.3 (830 %) — an inversion
artifact, not a units bug. The schema does not cap IV; T07/T08 should decide what to do with
implausible values rather than the provider silently dropping them.

**Fields to drop.** Cboe also sends `rho`, `theo`, `change`, `open`, `high`, `low`, `tick`,
`percent_change`, `prev_day_close`, `bid_size`, `ask_size`. The model is `extra="forbid"`, so
the adapter must select fields explicitly.

**Spot** is `data.current_price`. **Roots** arrive unpadded (`SPX`, `SPXW`) and, for indices,
the request symbol takes an underscore prefix (`_SPX.json`) while `data.symbol` comes back as
`^SPX` — neither is the contract root.
