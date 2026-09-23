# The GEX engine

`backend/app/modules/gex/gex/engine.py` is the product — every number the dashboard shows is computed
there. Its module docstring is the authoritative long-form reference; this file is the map to
it. `greeks.py` is the Black-Scholes/Black-76 layer beneath it.

**Both modules are pure.** No HTTP, no database, no filesystem, no logging. Keep them that
way: purity is what lets the capture job, the read API and a future backtest share them.

## The core quantity

```
contract_gex = dealer_sign · gamma · open_interest · multiplier · spot² · 0.01
```

Units: **US dollars of dealer delta acquired per +1 % move in the underlying.** Positive net
GEX = dealers buy weakness and sell strength (pinning, vol-suppressing). Negative = they
chase the move.

- `gamma` — spot gamma, unsigned, **recomputed** from the contract's own IV by `greeks.py`.
  Vendor gamma is a cross-check only (`use_vendor_gamma=True`), never the default; it also
  cannot be evaluated at a hypothetical spot, so the gamma *profile* always recomputes.
- `multiplier` — read per contract, never hardcoded to 100, so an adjusted contract cannot
  silently misprice.
- `spot² · 0.01` — converts "delta per 1.00 of index" into "dollars of delta per 1 % move".

## Sign discipline

The classic bug here is a doubled sign. The table the engine docstring keeps:

| | sign |
|---|---|
| `greeks.gamma()` | unsigned, ≥ 0 for calls and puts alike |
| `OptionContract.gamma` (vendor) | unsigned |
| `contract_gex()` | **signed** — +1 calls, −1 puts; the sign enters *exactly here* |
| `StrikeGex.call_gex` | ≥ 0 always |
| `StrikeGex.put_gex` | ≤ 0 always |
| `StrikeGex.net_gex` | `call_gex + put_gex` |
| `*.abs_gex` | Σ\|contract gex\|, sign-blind gamma concentration |

## Open interest: None ≠ 0

`None` means *unknown* → the contract is **excluded**. `0` means genuinely zero → the
contract is **included** and contributes exactly 0. Collapsing them silently invents open
interest. The same None-vs-zero rule governs nullable columns in `gex_levels`: a missing wall
reads back as `None`, never `0`.

## Time, expiry and settlement

- Time to expiry is measured from `snapshot.captured_at` — the effective time of the *data*,
  not the wall clock.
- Settlement fixes the expiry *time*: AM-settled (root `SPX`) at 09:30 New York, PM-settled
  (`SPXW`, `SPY`, `QQQ`) at 16:00. On one calendar date those differ by 6.5 hours, which
  dominates a 0DTE gamma. `settlement` is read per contract, never inferred from the date.
- **Expired contracts are dropped** (`greeks.is_expired`, mask recorded in `to_frame`).
  `time_to_expiry` floors T at one minute, so an expired contract would otherwise look like a
  fresh one-minute option and inject a huge spurious ATM spike.
- SPX and SPXW merge into a single `SPX` underlying and sum into the same strike bucket —
  intended: dealers are short gamma against both series.

## Implied-volatility policy

Live SPX chains carry vendor IVs from 0.054 up to ~8.3 (830 %) — inversion artifacts on 0DTE
wings quoted 0.00/0.05 and on deep-ITM contracts, plus ~1,900 contracts reporting Cboe's
`iv: 0.0` sentinel (mapped to `None`).

`IvPolicy(mode, iv_min=0.01, iv_max=3.0)` with `IvPolicyMode` ∈ `EXCLUDE` (default) /
`CLAMP` / `KEEP`. Under **every** mode a contract with no IV at all is excluded — gamma is
not computable without a vol. `DEFAULT_IV_POLICY` is what every public entry point uses.

## API surface

`compute_all(snapshot, filters, ...) -> GexResult` is the front door: flatten → filter →
per-contract GEX → per-strike and per-expiry aggregates → ±10 % gamma profile → flip point →
key levels → diagnostics. Building blocks, usable alone: `to_frame`, `expiry_mask`,
`include_mask`, `contract_gex`, `abs_gex`, `by_strike`, `by_expiry`, `gamma_profile`,
`flip_point`, `key_levels`.

Reuse a single `to_frame` result across filters — flattening, not aggregation, is the
expensive step. `store.compute_and_store` already does this.

`ExpiryFilter`: `ALL`, `ZERO_DTE`, `THIS_WEEK`, `MONTHLY_ONLY`, `EX_ZERO_DTE`.
Persisted on every capture: `ALL`, `ZERO_DTE`, `EX_ZERO_DTE` (`store.DEFAULT_FILTERS`).

Result records are frozen slotted dataclasses: `StrikeGex`, `ExpiryGex`, `ProfilePoint`,
`KeyLevels`, `GammaProfile`, `GexDiagnostics`, `SnapshotMeta`, `GexResult`. `_f()` converts
NaN/inf to `None` rather than a fake `0`.

## Walls vs. per-side maxima

`call_wall` / `put_wall` are **net-GEX-based**; `max_call_gex_strike` /
`max_put_gex_strike` are the per-side reading. They are different numbers and must not be
confused — both are persisted under distinct names so the distinction survives storage.
`docs/validation.md` §4.4 shows the net-based definition matches what vendors publish and the
per-side alternative demonstrably does not.

## Greeks

`greeks.py` is vectorized NumPy: `forward`, `d1`, `d2`, `black76_price`, `price`, `delta`,
`gamma`, `vega`, `vanna`, `charm`, `theta`, plus the time helpers `expiry_datetime`,
`time_to_expiry`, `is_expired`, `call_put_sign`. Forward is built as
`spot · exp((RISK_FREE_RATE − DIVIDEND_YIELD) · T)`; both rates are **parameters**
(`settings.RISK_FREE_RATE`, `settings.DIVIDEND_YIELD`), never fetched from a rates feed.

## Validation

`docs/validation.md` is the external check against public vendor GEX figures: sign convention
confirmed four ways, contract scope matched, OI vintage confirmed, every remaining difference
attributed. Read §7 for the verdict and §5 for the one partly-unexplained gap (SPY magnitude).
Internal cross-checks live in §6: recomputed vs. Cboe-published gamma contract by contract,
put-call parity on real quotes, end-to-end arithmetic.
