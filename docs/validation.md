# External validation of the GEX engine

**Task:** TASKS.md T10. **Date of the comparison:** Friday 2026-09-04, after the US cash close.
**Engine under test:** `backend/app/gex/engine.py` + `backend/app/gex/greeks.py` at the commit that
carries this file.

Every other test in this repository proves the engine is *self-consistent*: the Greeks match
hand-computed Black-Scholes, the aggregates match a synthetic chain built to a known answer, the
round-trips preserve their data. None of them can detect a systematically wrong *convention* — an
inverted sign, a missing expiry series, a stale open-interest vintage, a multiplier off by 100 —
because such an error is applied uniformly and every internal check still balances. This document
is the only external check in the project. It exists to answer four questions:

1. Is the dealer sign convention the one the market uses?
2. Are we counting the same contracts vendors count?
3. Is our open interest the same vintage?
4. Does our net-based wall definition match what vendors publish?

The short answers are **yes, yes, yes (exactly — to the contract), and yes for the vendor that
publishes bracketing walls**. The long answers, including everything that did *not* match, are
below.

---

## 1. What was captured

A single live capture from the Cboe delayed-quotes feed, taken after the close so that vendor
pages were showing settled end-of-day figures rather than values moving under the comparison.

| | SPX | SPY | QQQ |
|---|---|---|---|
| Spot | 7718.60 | 769.72 | 717.93 |
| `captured_at` | 2026-09-04 21:43:46 UTC (17:43 ET) | 21:43:32 UTC | 21:43:28 UTC |
| Source | `cboe`, 15-min delayed | same | same |
| Contracts | 28,650 | 12,456 | 11,006 |
| Expiries (live) | 55 of 56 | 32 of 33 | 32 of 33 |
| Strikes | 806 | 483 | 523 |
| Contracts admitted | 27,400 | 10,996 | 9,299 |
| Roots | SPX 9,126 / SPXW 19,524 | SPY | QQQ |

The expiring 2026-09-04 series is dropped as expired in every figure below — correctly, since the
capture is after 16:00 ET. This matters for the comparison and is revisited in §5.3.

---

## 2. Sources actually reached

Vendor pages are frequently JavaScript-rendered, partly paywalled, or behind a sign-up wall.
This section records exactly what was and was not obtainable, because a validation document that
launders a guess into apparent evidence is worse than one that admits a gap.

| Source | URL | Outcome |
|---|---|---|
| **Vendor A — GEX Metrix** | `gexmetrix.com/gex/{spx,spy,qqq}` | **Full numbers + scope + open interest.** Server-rendered; read directly. |
| **Vendor B — InsiderFinance** | `insiderfinance.io/gamma-exposure[/SPX]` | **Full numbers + open interest.** Server-rendered; read directly. |
| **Vendor C — FlashAlpha** | `flashalpha.com/tools/gamma-exposure` | **Methodology only.** Formula, sign convention, wall definition and OI vintage stated in page text; live numbers are behind the interactive tool. |
| **Vendor D — SpotGamma** | `spotgamma.com/free-tools/spx-gamma-exposure/` | **Methodology only.** The chart is JS-rendered; no numbers readable. The sign-convention sentence *is* readable and is quoted below. |
| **Vendor E — AlgoStorm** | `algostorm.com/gex/` | **Methodology only.** Page served with `Calculating…` placeholders; formula stated in its documentation text. |
| Unusual Whales | `unusualwhales.com/stock/SPX/greek-exposure` | **Not reached.** Page returns navigation metadata only; data is behind JS/login. |
| ZeroGEX | `zerogex.io/spx-gamma-levels` | **Not reached.** HTTP 403. |
| QuantWheel | `quantwheel.com/tools/gex/spx` | **Not reached.** Interactive calculator, three free runs/day, no static figures in the page. |

TASKS.md names Unusual Whales and SpotGamma as examples; neither yielded a live number. Two other
free, no-login vendors did, and three more yielded published methodology that can be compared
against our *conventions* even without a number. All vendor figures below were read by automated
page fetch on 2026-09-04 and are quoted as the pages served them; they have not been independently
screenshot-verified.

**Vendor A's scope is narrower than ours by design.** It states it computes over
"option series expiring between today and the current monthly OpEx (2026-09-18)" — 5,030 series for
SPX, 2,220 for SPY, 2,199 for QQQ. Every comparison against Vendor A below therefore uses our
matching `expiries <= 2026-09-18` subset as well as our headline all-expiry figure. Vendor B
publishes no scope statement, but its open interest (§5.2) shows it aggregates the full chain.

---

## 3. The comparison table

Our figures are the engine defaults: recomputed spot gamma (Black-76 on the forward for SPX,
Black-Scholes-Merton for SPY/QQQ), `r = 4.0%`, `q = 1.3%`, calendar-day/365 time, per-contract
multiplier, SPX and SPXW merged, expired contracts dropped, IV band `[0.01, 3.0]` with the
excluded contracts accounted for separately. Units are **US$ billions of dealer delta per +1 %
move**, the same units all three vendors quote.

### 3.1 SPX

| Metric | Ours (all expiries) | Ours (≤ 2026-09-18) | Vendor A (GEX Metrix) | Vendor B (InsiderFinance) | Diff vs A | Diff vs B |
|---|---:|---:|---:|---:|---:|---:|
| Spot | 7718.60 | 7718.60 | 7718.6 | 7717.81 | 0.00 | +0.79 |
| Scope | 55 expiries | 9 expiries | today→OpEx | full chain | — | — |
| **Net GEX** | **+48.29 B** | **+18.10 B** | **+3.66 B** | **+43.6 B** | +14.44 B (A vs our matched scope) | +4.69 B (**+10.8 %**) |
| Call GEX | +316.71 B | +160.97 B | n/p | +345.1 B | — | −28.4 B (−8.2 %) |
| Put GEX | −268.42 B | −142.87 B | n/p | −301.5 B | — | +33.1 B (−11.0 %) |
| Abs (total) GEX | 585.14 B | 303.85 B | n/p | 646.7 B | — | −61.6 B (−9.5 %) |
| **Call wall** | **7800** | 7800 | 7700 | **7800** | −100 | **exact match** |
| **Put wall** | **7500** | 7500 | 7700 | **7500** | +200 | **exact match** |
| Max call-side strike | 8000 | 7800 | (= their call wall 7700) | — | — | — |
| Max put-side strike | 8000 | 7700 | (= their put wall 7700) | **7700 reproduced** | — | — |
| **Flip / zero gamma** | **7661.73** | 7692.34 | 7721 | 7680.96 | −28.7 (A) | −19.2 (**−0.25 %**) |
| Call OI (live) | 9,627,036 | 3,059,786¹ | n/p | 9,659,775 | — | −0.34 % |
| Put OI (live) | 13,445,491 | 4,537,355¹ | n/p | 12,976,103 | — | +3.6 % |
| Total OI | 23,072,527 | 7,597,141¹ | n/p | 22,635,878 | — | +1.9 % |

¹ including the expired 2026-09-04 series, for comparability with Vendor A — see §5.2.

### 3.2 SPY

| Metric | Ours (all expiries) | Ours (≤ 2026-09-18) | Vendor A | Vendor B | Diff vs A | Diff vs B |
|---|---:|---:|---:|---:|---:|---:|
| Spot | 769.72 | 769.72 | 769.47 | 769.93 | +0.25 | −0.21 |
| **Net GEX** | **−2.298 B** | **−1.911 B** | **−11.18 B** | **−19.4 B** | +9.27 B | +17.1 B |
| Call GEX | +18.18 B | +8.96 B | n/p | +36.7 B | — | −18.5 B |
| Put GEX | −20.48 B | −10.87 B | n/p | −56.1 B | — | +35.6 B |
| Abs (total) GEX | 38.66 B | 19.83 B | n/p | 92.8 B | — | −54.1 B (**2.40×**) |
| **Call wall** | **780** | 780 | 770 | **780** | +10 | **exact match** |
| **Put wall** | **760** | 760 | 770 | **760** | −10 | **exact match** |
| Max call-side strike | 780 | 770 | **770 reproduced** | — | 0 | — |
| Max put-side strike | 760 | 760 | 770 | — | — | — |
| **Flip / zero gamma** | **772.01** | 772.27 | 785.10 | 768.25 | −13.1 | +3.76 (+0.49 %) |
| Call OI | 5,207,189 | **1,773,317**¹ | **1,773,317** | 5,436,478 | **0 (exact)** | −4.2 % |
| Put OI | 13,443,183 | **4,487,736**¹ | **4,487,736** | 12,768,470 | **0 (exact)** | +5.3 % |
| Total OI | 18,650,372 | **6,261,053**¹ | **6,261,053** | 18,204,948 | **0 (exact)** | +2.4 % |

### 3.3 QQQ (bonus — Vendor A publishes it, Vendor B does not)

| Metric | Ours (all) | Ours (≤ OpEx) | Vendor A | Diff |
|---|---:|---:|---:|---:|
| Spot | 717.93 | 717.93 | 718.04 | −0.11 |
| Net GEX | −0.680 B | +0.267 B | −0.62 B | +0.89 B |
| Call wall | 730 | 730 | 718 | +12 |
| Put wall | 700 | 700 | 718 | −18 |
| Max call/put-side strike (0DTE alive) | — | **718 / 718** | **718 / 718** | **0 (exact)** |
| Flip | 719.55 | 717.09 | 721 | −1.45 |
| Call OI | — | **1,600,187**¹ | **1,600,187** | **0 (exact)** |
| Put OI | — | **2,313,830**¹ | **2,313,830** | **0 (exact)** |

---

## 4. The four questions, answered

### 4.1 Is our sign convention the market's? — **Yes. Confirmed four ways.**

PLAN.md §3 assumes dealers are long calls and short puts, so calls contribute positive GEX and
puts negative. That is a claim about *positioning*, not a fact, and if vendors assumed the opposite
our net GEX would carry the wrong sign and the dashboard's regime language would be exactly
inverted. It does not.

Three vendors state the convention explicitly, in their own words:

- **SpotGamma:** "This is a standard net gamma curve, using basic assumptions that options
  liquidity providers are short put options and long calls."
- **FlashAlpha:** "Call gamma as positive and put gamma as negative, which is equivalent to
  assuming dealers are long calls and short puts."
- **AlgoStorm:** "`GEX = OI × Gamma × Spot² × Multiplier`. Call GEX is positive; put GEX is
  negative."
- **GEX Metrix** publishes the formula outright:
  `GEX per option = gamma × OI × 100 × spot² × 0.01, puts negative`.

That last line is `contract_gex` character for character, including the `spot² × 0.01`
normalization to a 1 % move and the multiplier of 100. FlashAlpha states the same formula.

The numbers agree as well, and this is the stronger evidence because it is not a copied
definition. **All three sources with live figures independently report SPX net GEX positive and
SPY net GEX negative on the same day** — ours +48.29 / −2.30 B, Vendor A +3.66 / −11.18 B,
Vendor B +43.6 / −19.4 B. Had our sign been inverted we would have disagreed with both vendors on
both symbols. The SPX-positive / SPY-negative split is itself a real structural fact rather than
noise: SPY's put open interest is 2.6× its call open interest (13.44 M vs 5.21 M) because SPY is
the retail and institutional hedging vehicle, while SPX's book is far more balanced
(13.45 M vs 9.63 M).

**Confidence: high.** Convention text and live sign agree across five independent sources.

### 4.2 Are we counting the same contracts? — **Yes, once scope is matched.**

Vendors differ enormously on scope, and this is by far the largest driver of headline-number
disagreement — larger than any modelling choice.

- **Vendor A truncates at the front monthly OpEx.** It says so: "option series expiring between
  today and the current monthly OpEx (2026-09-18)". Our matching subset is 9 expiries out of 55.
  Restricting to it takes our SPX net GEX from +48.29 B to +18.10 B — a **62 % reduction from the
  expiry set alone**. Comparing our all-expiry number to Vendor A's front-month number and calling
  the gap an error would have been the single easiest mistake to make in this exercise.
- **Vendor B aggregates the full chain**, which its open interest confirms (§5.2).
- **SPX and SPXW are merged by both us and Vendor B.** This is worth stating because it could have
  been a factor-of-four error: the AM-settled `SPX` root carries 18.28 M contracts of OI across
  9,126 series, and the PM-settled `SPXW` weeklies carry 5.67 M across 19,524 series. A vendor
  quoting SPXW only, or SPX only, would be off by 76 % or 24 % respectively. Vendor B's total SPX
  OI of 22.64 M is within 1.9 % of our merged 23.07 M, which is only possible if both roots are in.
- **0DTE.** On this after-close capture the 2026-09-04 series is expired and excluded, in ours and
  in Vendor B's figures. Vendor A still counts it (§5.2, §5.3).

**Confidence: high** for Vendor A (scope is published and OI matches exactly), **high** for
Vendor B on SPX (OI within 1.9 %), **high** for Vendor B on SPY OI (within 2.4 %) though not for
its SPY *magnitude* (§5.4).

### 4.3 Is our OI the same vintage? — **Yes. This is the cleanest result in the document.**

Open interest publishes overnight from the OCC and reflects the prior session's close; FlashAlpha
states this convention explicitly ("Open interest is reported end-of-day by the OCC and reflects
the previous session's close"). If we were reading a different vintage — or double counting, or
dropping a series — the totals would drift.

They do not drift at all. Over Vendor A's exact expiry window (today through 2026-09-18, today's
expiring series included), our totals and Vendor A's published totals are **identical to the
contract**:

| | Our calls | Vendor A calls | Our puts | Vendor A puts | Our total | Vendor A total |
|---|---:|---:|---:|---:|---:|---:|
| SPY | 1,773,317 | **1,773,317** | 4,487,736 | **4,487,736** | 6,261,053 | **6,261,053** |
| QQQ | 1,600,187 | **1,600,187** | 2,313,830 | **2,313,830** | 3,914,017 | **3,914,017** |

Two symbols, four independent seven-digit numbers, zero difference. Both sides very likely read
the same Cboe delayed feed, so this does not prove the OCC file itself is right — but it proves our
ingestion, our root mapping, our expiry parsing, our call/put classification and our
`open_interest is None` handling introduce **no** distortion whatsoever between the feed and the
number the engine sums. Against Vendor B, which does not publish a scope, agreement is 1.9 % (SPX)
and 2.4 % (SPY) on totals across the whole chain.

**Confidence: very high.**

### 4.4 Does the net-based wall definition match what vendors publish? — **Yes, and the per-side alternative is now demonstrably the one that fails.**

This is the definition T08 deliberately changed, so it deserved the sharpest test. It got one.

**Vendor B's walls match ours exactly, on both symbols:** SPX call wall 7800 and put wall 7500;
SPY call wall 780 and put wall 760. Four strikes, four exact matches, on two different underlyings
with different strike grids. Vendor C (FlashAlpha) publishes the matching definition in words —
"call wall (strike with the highest positive GEX)" and "put wall (strike with the most negative
GEX)" — i.e. extrema of the signed per-strike total, which is exactly `argmax`/`argmin` of
`net_gex`.

**Vendor A uses the per-side definition, and it collapses exactly as T08 predicted.** Its wall
language is per-side — "the strike carrying the heaviest call gamma" and "the strike with the
highest put gamma concentration" — and its published walls are **7700 / 7700 for SPX, 770 / 770 for
SPY, and 718 / 718 for QQQ**. Every one is a single strike serving as both walls, bracketing
nothing.

We reproduced those collapsed walls from our own chain by switching to the per-side reading on the
matching contract set:

| | Vendor A call wall | Vendor A put wall | Our `max_call_gex_strike` | Our `max_put_gex_strike` |
|---|---:|---:|---:|---:|
| SPY (their scope, today's expiry alive) | 770 | 770 | **770** | **770** |
| QQQ (their scope, today's expiry alive) | 718 | 718 | **718** | **718** |
| SPX (their scope, vendor gamma) | 7700 | 7700 | **7700** | **7700** |

QQQ 718 is the persuasive one: it is not a round number, it is not the spot (717.93 rounds to 718
but the strike grid has 715 and 720 as well), and both of Vendor A's walls land on it while our
per-side fields land on it too. Six of six per-side extrema reproduced.

So the wall difference between us and Vendor A is **fully attributed to definition, not to any
error on either side** — and the definition T08 chose is the one that (a) matches the vendor that
publishes bracketing walls, (b) matches the published wording of a third vendor, and (c) does not
degenerate. The per-side numbers remain available as `max_call_gex_strike` / `max_put_gex_strike`,
which is what a user comparing against a GEX Metrix screenshot will need.

**Confidence: high.**

---

## 5. Every material difference, attributed

### 5.1 SPX net GEX: ours +48.29 B vs Vendor B +43.6 B (+10.8 %)

Within the band that the free parameters of any GEX model span. The dominant lever is not the
gamma formula but the **cost of carry** used to build the forward:

| SPX variant | Net GEX |
|---|---:|
| `r = q = 0` (no carry at all — what a simple public tool does) | **+36.57 B** |
| Vendor B's published figure | **+43.6 B** |
| Ours, `r = 4.0 %`, `q = 1.3 %` (engine default) | **+48.29 B** |
| Ours, `q = 0.41 %` (put-call-parity-implied carry, §6.2) | **+52.25 B** |
| Ours, Cboe's published gamma instead of ours | **+41.83 B** |

Vendor B's 43.6 B sits inside the 36.6 – 52.2 B range spanned by carry assumptions alone, and is
within **4 %** of the figure we get using the vendor's own published gammas. There is no residual
here that needs a bug to explain it. (Note also that PLAN.md §1's own research figure of ≈ +52 B is
reproduced almost exactly by the parity-implied carry.)

The uniform ~9–11 % shortfall in our call, put and absolute GEX versus Vendor B points the same
way: a systematically slightly-lower gamma across the whole chain, consistent with our forward
being slightly low (§6.2), not with any per-contract error.

### 5.2 Vendor A's magnitudes: ours +18.10 B vs +3.66 B on SPX (matched scope)

Two causes, in order of size.

**(a) Vendor A counts the expired 0DTE series; we do not.** This is provable from the open interest
identity in §4.3: our totals match Vendor A's exactly *only when today's expiring series is
included*. Excluding it, our SPY total drops to 5,323,828 against their 6,261,053 — a difference of
937,225, which is exactly the open interest of the expiring 2026-09-04 SPY series. Vendor A's
snapshot is stamped 16:44 ET, after the 16:00 expiry.

**(b) 0DTE gamma near expiry is not a number, it is a singularity.** As `T → 0`, at-the-money gamma
diverges. `greeks.time_to_expiry` floors `T` at one minute, and `engine.to_frame` therefore
consults `greeks.is_expired` *before* `t`, precisely so a dead contract cannot masquerade as a
fresh one-minute option with enormous ATM gamma. Rerunning our engine with the "as of" instant
moved back inside the session — which makes the expiring series live — shows how violently the
answer moves in the final minutes:

| SPY, as-of (NY) | Net GEX (≤ OpEx) | of which 0DTE |
|---|---:|---:|
| 10:00 | −5.82 B | −3.93 B |
| 12:00 | −6.59 B | −4.69 B |
| 15:00 | −9.39 B | −7.48 B |
| 15:45 | −7.76 B | −5.86 B |
| 15:59 | −1.73 B | +0.18 B |

The 0DTE contribution alone swings from −7.5 B to +0.2 B within a single session, and reverses sign
in the last quarter hour. Vendor A's −11.18 B is in this family; it is not reproducible from a
post-close chain, because reproducing it would require the intraday IVs that existed at their
snapshot instant, which a closing chain does not contain.

**This is a difference in the "as of" instant and in expired-contract handling, not an arithmetic
disagreement.** Our treatment is the defensible one for an end-of-day product: an expired contract
has no gamma, and a level computed from one is fiction. Note also that Vendor A's own SPX page is
internally inconsistent on this date — it reports net GEX **positive** (+3.66 B) while its prose
says "dealers are in net negative gamma" because spot sits below its zero-gamma level of 7721.
Both cannot be true. That inconsistency is a reason to weight Vendor A's magnitudes lightly while
still weighting its *scope and open-interest* disclosures heavily, since those matched us exactly.

### 5.3 Today's SPX 0DTE is invisible to any post-close computation

Worth recording because it explains why SPX survived the after-close comparison better than SPY
did. On this capture the expiring SPX series has 626 rows and 876,624 contracts of open interest,
but **610 of the 626 report Cboe's `iv: 0.0` "could not invert" sentinel and 616 report gamma
exactly 0.0**. There is no vol to reprice with and no vendor gamma to fall back on. Any engine —
ours or a vendor's — that tries to value that series from a closing chain is working from nothing.
Our diagnostics say so out loud rather than silently contributing zero: the series appears in
`missing_iv` and in `missing_iv_gex_vendor`. SPY's expiring series is in better shape (318 of 490
carry an IV), which is why SPY's 0DTE *can* be revived in §5.2 and SPX's cannot.

### 5.4 SPY magnitude vs Vendor B: ours 38.66 B absolute vs 92.8 B (2.40×) — **partly unexplained**

This is the one material difference this document cannot fully attribute, and it is stated as such
rather than rationalized.

What is *not* the cause: open interest (ours and theirs agree within 2.4 %), the sign convention
(we agree on the sign), the walls (exact match), the multiplier (100 on every contract, verified),
or the IV filter (turning it off moves SPY net GEX by less than $1 M).

What is partly the cause: the "as of" instant. Recomputing SPY with the expiring series alive at
15:45 ET raises our absolute GEX from 38.66 B to 59.75 B, closing about 55 % of the gap. Vendor B
publishes no timestamp, and its spot of 769.93 differs from our closing 769.72, consistent with an
intraday rather than settled snapshot.

What remains: roughly a 1.55× residual on SPY that no examined lever reproduces. Two observations
bear on which side is likelier to be off:

- The ratios are not uniform — Vendor B's call GEX is 2.02× ours while its put GEX is 2.74× ours —
  so this is a composition difference, not a scaling constant, and therefore not a units error.
- Vendor B's own two pages are not mutually consistent. Dollar gamma per contract scales
  approximately with the underlying's level, so a single methodology applied to both books should
  give an SPX:SPY absolute-GEX ratio near `(OI × spot)_SPX / (OI × spot)_SPY` = **12.4**. Ours is
  **15.1** (SPX's book is somewhat more ATM- and short-dated-concentrated, which raises it).
  Vendor B's is **7.0** — its SPY page is roughly 1.8× more gamma-dense per unit of OI×spot than its
  own SPX page. Since its SPX page agrees with us within 11 %, the SPY page is the likelier outlier.

That is an argument, not a proof. **The honest statement is: our SPY magnitude is corroborated by
nothing external. Its sign, its walls and its open interest are corroborated; its size is not.**

### 5.5 Flip point: the least reproducible number in the set

| | Ours | Vendor A | Vendor B | Spread |
|---|---:|---:|---:|---:|
| SPX | 7661.73 (−0.74 % from spot) | 7721 (+0.03 %) | 7680.96 (−0.49 %) | 59 pts, and A is on the *other side of spot* |
| SPY | 772.01 (+0.30 %) | 785.10 (+2.00 %) | 768.25 (−0.22 %) | 16.9 pts, straddling spot |

Ours and Vendor B agree on SPX to 0.25 % of spot and place the flip on the same side of spot;
Vendor A disagrees with both in direction. On SPY all three disagree, with two different signs of
"distance from spot".

This is not evidence of an error anywhere. The flip is the output of a *curve*, and every
methodology choice that barely moves net GEX moves the flip visibly:

| SPX flip under our own defensible variants | |
|---|---:|
| Default | 7661.73 |
| `r = q = 0` | 7676.73 (+15 pts) |
| Parity-implied carry (`q = 0.41 %`) | 7656.48 (−5 pts) |
| Vendor A's expiry scope | 7692.34 (+31 pts) |
| Monthlies only | 7643.96 (−18 pts) |

Our own flip moves 48 points across parameter choices we would each defend — more than our
19-point gap to Vendor B. Vendor A additionally uses a different construction entirely: a
"Gaussian-weighted profile spanning ±15 % of spot", against our unweighted ±10 % grid in 0.1 %
steps with linear interpolation at the sign change and selection of the crossing nearest spot.

**A caveat this comparison surfaced, worth carrying into the dashboard:** our gamma profile holds
each contract's implied volatility fixed as hypothetical spot moves, so it does not roll along the
skew. For a 10 % downside move the true vol surface would be considerably higher, which would
flatten gamma there. The flip point should be read as a level with a tolerance of tens of SPX
points, not as a price. That is a documented modelling choice (`engine.gamma_profile`), not a
defect, and it is consistent with what vendors do; but the dashboard should not render it to two
decimal places without a band.

### 5.6 Spot differences

Vendor spots differ from ours by 0.79 SPX points (0.010 %), 0.21 SPY points (0.027 %) and 0.11 QQQ
points (0.015 %). GEX scales with `S²`, so these contribute at most 0.05 % and explain nothing.
They are consistent with the vendors' snapshots being taken minutes apart from ours.

---

## 6. Internal cross-checks (no vendor required)

These do not depend on a vendor page being reachable, and they are the strongest evidence in the
document that the per-contract arithmetic is right.

### 6.1 Our gamma vs Cboe's published gamma, contract by contract

`contract_gex(..., use_vendor_gamma=True)` exists for exactly this. Comparing our recomputed spot
gamma against the vendor's, on every admitted contract whose published gamma exceeds Cboe's
four-decimal rounding floor:

| | n | Median ratio (ours ÷ Cboe) | OI-weighted mean | p5 | p95 |
|---|---:|---:|---:|---:|---:|
| SPX | 3,598 | **0.9972** | **0.9978** | 0.896 | 1.122 |
| SPY | 5,801 | 0.9751 | 0.9596 | 0.815 | 1.125 |
| QQQ | 5,135 | 0.9870 | 0.9739 | 0.749 | 1.041 |

Two independent implementations, agreeing to 0.2 % on SPX open-interest-weighted. The residual is
not random — it is concentrated exactly where theory says it should be:

| Median ratio by DTE | 0–7 | 7–30 | 30–90 | 90–365 | > 365 |
|---|---:|---:|---:|---:|---:|
| SPY | 0.964 | 0.982 | 0.983 | 0.979 | **0.901** |
| QQQ | 1.002 | 0.994 | 0.992 | 0.975 | **0.915** |

The gap opens at long tenor, which is where the carry assumption bites — and §6.2 independently
shows our carry is too low there. The two findings agree.

### 6.2 Put-call parity on real market quotes

Parity gives the forward without any model: `F = K + (C − P)·e^{rT}`. Taking the median across the
five strikes nearest spot at every expiry, using bid/ask mids:

| Implied carry `r − q`, tenors > 3 months | SPX | SPY | QQQ |
|---|---:|---:|---:|
| Market-implied (median) | **3.59 %** | **3.17 %** | **3.80 %** |
| Engine default (`r 4.0 % − q 1.3 %`) | 2.70 % | 2.70 % | 2.70 % |

Our forward is therefore **too low at long tenor** — by 1.0 % at one year and 2.6 % at two years on
SPX. This is a genuine finding, and it is the same finding as §6.1's long-dated gamma gap seen from
the other side. It moves SPX net GEX from +48.29 B to +52.25 B (+8 %). It is a **parameter**
choice, not a bug: TASKS.md T07 specifies that the rate is a parameter and is never fetched from a
rates feed, and 1.3 % is a reasonable trailing S&P dividend yield. What the parity data really says
is that the market's *implied financing* rate on index options exceeds 4 % — a well-known feature
of index option markets, not something a dividend-yield constant can express. Recommended
follow-up, out of scope for T10: fit `r − q` per expiry from parity rather than assuming a global
constant (this also becomes available for free in Phase 5, where quotes stream).

At short tenor the parity forward sits ~0.10 % *below* our model forward on SPX, i.e. the option
mids imply an index level about 6 points under the 7718.60 index print. That is an
index-print-versus-option-quote timing mismatch, not a modelling error, and it is immaterial to GEX
(≈0.02 % on an `S²` term).

SPY parity is essentially exact at short tenor (implied forward within 0.01–0.02 % of ours), which
independently confirms the Black-Scholes-Merton path for ETFs.

### 6.3 End-to-end arithmetic

The single largest contract on the SPX chain — `SPX261016C08000000`, K = 8000, T = 0.11413 y,
IV = 10.44 %, OI = 146,268, carrying 1.44 % of the whole chain's absolute gamma — was recomputed
from first principles outside the engine (forward, `d1`, normal pdf, `e^{-qT}·φ(d1)/(S·σ√T)`, then
`sign · Γ · OI · 100 · S² · 0.01`):

```
engine  $8,426,060,531.360949
by hand $8,426,060,531.360902
relative difference 5.5e-15
```

Multipliers were verified as 100 on all 52,112 contracts across the three symbols; no adjusted
contract with a non-standard multiplier is present today, and the engine reads the field per
contract rather than hardcoding it, so one would not be silently mispriced.

### 6.4 The IV filter is immaterial to the comparison

The `[0.01, 3.0]` IV band excludes 12 SPX contracts and moves SPX net GEX by **−$37 K on
$48.29 B** — 0.00008 %. `net_gex_iv_unfiltered` is reported alongside for exactly this
reconciliation. No vendor comparison in this document turns on it.

---

## 7. Verdict, and what was changed

**No engine change was made, and no regression test was added, because no discrepancy traced to a
bug.** Every material difference is attributed to a specific, identified cause:

| Difference | Cause |
|---|---|
| Vendor A's net GEX far below ours | **Expiry set** (front-month only: −62 % on SPX) plus **expired-0DTE inclusion** |
| Vendor A's walls collapsed onto one strike | **Wall definition** (per-side, not net) — reproduced exactly from our own data |
| Vendor B's SPX net GEX 11 % below ours | **Carry parameter** (`r − q`); Vendor B sits inside our own 36.6–52.2 B parameter band and within 4 % of our vendor-gamma variant |
| Vendor B's SPY magnitude 2.4× ours | **Partly the "as of" instant** (0DTE liveness closes ~55 %); **the remainder is unexplained** |
| Flip point spread of 19–59 points | **Profile construction** (grid width, weighting, expiry scope, carry); exceeded by our own parameter sensitivity |
| Long-dated gamma ~10 % under Cboe's | **Carry parameter** — corroborated independently by put-call parity |

TASKS.md T10's acceptance criterion — "net GEX sign and walls agree with at least one vendor within
the explained tolerances" — is met, and comfortably: the sign agrees with **both** vendors on
**both** symbols, and the walls agree with Vendor B **exactly** on all four values.

### Confidence, stated plainly

| Claim | Confidence | Basis |
|---|---|---|
| Sign convention is the market's | **High** | Four vendors state it; three live sources agree on sign for two symbols |
| Formula and units (`Γ·OI·100·S²·0.01`, $ per 1 % move) | **High** | Two vendors publish it verbatim |
| Contract set / SPX+SPXW merge | **High** | Vendor B's OI within 1.9 % of our merged total |
| OI vintage and ingestion fidelity | **Very high** | Four seven-digit totals matched exactly across two symbols |
| Net-based wall definition | **High** | Exact match to Vendor B on 4/4; per-side alternative reproduced Vendor A's 6/6 collapsed walls |
| Per-contract gamma arithmetic | **Very high** | 5.5e-15 against hand computation; 0.998 OI-weighted against Cboe on SPX |
| **SPX net GEX magnitude** | **Medium-high** | Within 11 % of one vendor, bracketed by our own carry band; **one live vendor only** |
| **SPY net GEX magnitude** | **Low** | Sign, walls and OI corroborated; **magnitude corroborated by nothing** — 2.4× vs Vendor B, ~55 % explained |
| Flip point | **Low-medium** | Same side of spot as Vendor B on SPX, within 0.25 %; no agreement on SPY; our own sensitivity exceeds the vendor gap |
| Carry parameter `q = 1.3 %` | **Known to be off** | Parity implies `r − q` ≈ 3.6 % vs our 2.7 %; worth ~8 % of SPX net GEX. Parameter, not bug — see §6.2 |

**The one-line summary for someone acting on this:** the conventions are right — sign, formula,
units, multiplier, contract set, OI vintage and wall definition are all externally corroborated,
several of them exactly. The *level* of SPX net GEX is corroborated to about 10 % by a single
vendor and is materially sensitive to a carry parameter we now know is mis-set. The SPY level and
the flip point should be treated as internally consistent but externally unconfirmed until a second
capture on a different day, ideally taken intraday rather than after the close.

### Recommended follow-ups (not part of T10)

1. Fit `r − q` per expiry from put-call parity instead of a global constant (§6.2). Biggest single
   lever on net GEX that is currently a guess.
2. Repeat this comparison from an **intraday** capture, when 0DTE is live and all vendors are
   quoting the same regime. Half the unexplained SPY gap is an artefact of comparing a settled
   chain against what are probably intraday vendor snapshots.
3. Expose `max_call_gex_strike` / `max_put_gex_strike` in the dashboard next to the walls, labelled
   as the per-side reading, so a user comparing against a GEX Metrix screenshot can see both
   definitions rather than concluding one of them is broken.
4. Render the flip point with a tolerance band rather than two decimals (§5.5).

---

## 8. Reproducing this

```bash
cd backend
uv sync --all-groups
uv run python -m app.providers.cboe SPX      # and SPY, QQQ
```

Then, programmatically, against a `ChainSnapshot` from `CboeProvider().fetch_chain(...)`:

| Figure in this document | How to reproduce |
|---|---|
| Headline levels | `engine.compute_all(snap)` |
| Vendor A's scope | `engine.compute_all(snap, [e for e in expiries if e <= date(2026, 9, 18)])` |
| Per-side walls | `result.levels.max_call_gex_strike` / `.max_put_gex_strike` |
| Carry sensitivity | `engine.compute_all(snap, r=0.0, q=0.0)` / `q=0.0041` |
| Vendor-gamma cross-check | `engine.compute_all(snap, use_vendor_gamma=True)` |
| IV-filter reconciliation | `result.diagnostics.net_gex_iv_unfiltered` |
| 0DTE-alive reruns | `engine.to_frame(snap, now=<intraday instant>)`, then `compute_all(..., frame=...)` |

All vendor figures were read from the public pages listed in §2 on 2026-09-04.

---

## 9. GLD and DIA (T38) — the DIA carry caveat

T38 added GLD and DIA as tracked instruments (PLAN.md §1) through the existing pipeline
unchanged: both parse and compute correctly through `CboeProvider._parse_payload` and
`compute_all` with no engine, Greeks, schema or migration change. Both were verified live on
2026-09-05, both are P.M.-settled with a single vendor root equal to the ticker, and both
have *better* data quality than SPX on this capture (zero extreme-IV exclusions, zero missing
open interest):

| | contracts | expiries | net GEX | abs GEX | call wall | put wall | flip |
|---|---:|---:|---:|---:|---:|---:|---:|
| GLD | 7,546 | 29 | **+2.265 B** | 5.303 B | 415 | 335 | 381.45 |
| DIA | 5,028 | 21 | **−0.009 B** | 1.037 B | 540 | 533 | 532.58 (spot 532.34) |

**This section records a limitation, not a bug.** `DIVIDEND_YIELD` is a single global
`0.013` (the trailing S&P 500 yield), applied uniformly to every symbol via the forward
`spot * exp((r - q) * T)` (`app/gex/greeks.py`). That parameter is wrong for both new
instruments — GLD pays no dividend at all (it carries a ~0.40 % expense-ratio drag instead),
and DIA has its own yield, not the S&P's — and T33 (Opus, not yet landed) is what fits the
carry per expiry from put-call parity, for all five symbols at once. Measured impact of
`q = 0` instead of the current default, on this same capture:

| Symbol | Net GEX at `q = 0.013` (current) | Net GEX at `q = 0` | Flip at `q = 0.013` | Flip at `q = 0` |
|---|---:|---:|---:|---:|
| GLD | +2.265 B | +2.322 B (+2.5 %) | 381.45 | 380.65 |
| **DIA** | **−0.009 B** | **+0.008 B** | 532.58 | (shifts similarly) |

GLD's net GEX moves by a modest 2.5 % under this stress test — a bias, but the sign and the
signal survive it. **DIA does not: the carry assumption flips its net GEX's sign.** DIA's net
is only 0.9 % of its 1.037 B gross absolute gamma, so it is dominated by whichever small
residual the carry parameter happens to produce, not by a real directional imbalance between
DIA's calls and puts. Concretely, this means:

- **DIA's headline "dealers are net long/short gamma" reading is not trustworthy** at the
  current global `q = 0.013` until T33 fits the carry properly. Report it as noise-dominated,
  not as a directional signal, until then.
- **DIA's flip point (532.58, essentially at the 532.34 spot print) is likewise not a
  meaningful level** for the same reason — a flip this close to spot, on a net this small
  relative to gross, is exactly the "curve is nearly flat here" case §5.5 already describes
  for SPX/SPY, just more acute because DIA's net is two orders of magnitude smaller relative
  to its gross than either of those.
- **GLD's walls (415 call / 335 put) and DIA's walls (540 call / 533 put) are unaffected.**
  Per-strike net GEX *does* depend on the forward — every contract's gamma is computed
  from it — so the robustness here is not that walls bypass the carry term. It is that a
  wall is an `argmax`/`argmin` over a discrete strike grid, and a carry perturbation this
  small rescales neighbouring strikes by nearly the same factor, so the *ranking* rarely
  changes even though the levels do. Empirically, on this capture both symbols' walls are
  identical at `q = 0.013` and `q = 0` (GLD 415/335, DIA 540/533). Treat that as the
  measured result it is, not a structural guarantee: a wall contest already near a tie
  could flip under a large enough carry correction.

**Do not attempt to special-case DIA's dividend yield here.** T33 fixes the carry per expiry
from parity for all five symbols at once, which is the right level to fix it at; a one-off
`q` override for DIA alone would just trade one wrong global constant for one wrong
symbol-specific constant, and would need to be undone the moment T33 lands.

---

## 10. Sector and industry ETFs (T47) — live verification, dividend sensitivity, storage

T47 added 23 sector/industry ETFs as `Underlying` members and a second, 16:45 ET capture job
(`capture_extended_job`) separate from the core five's 16:20 EOD job. Every symbol below was
verified against the live Cboe endpoint
(`https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json`) on **2026-09-09**, the
way T38 verified GLD and DIA: fetch, confirm a 200 with a non-empty `options` array, confirm a
single vendor root equal to the ticker (no adjusted-option variant), confirm decimal-fraction
per-contract IV, and count missing open interest.

| Symbol | Sector/theme | Contracts | Expiries | Spot | Median IV | Missing OI |
|---|---|---:|---:|---:|---:|---:|
| XLK | Technology SPDR | 2,336 | 17 | 188.36 | 0.301 | 0 |
| XLF | Financials SPDR | 2,028 | 28 | 57.15 | 0.246 | 0 |
| XLE | Energy SPDR | 2,070 | 25 | 65.40 | 0.287 | 0 |
| XLV | Health Care SPDR | 1,464 | 13 | 166.77 | 0.196 | 0 |
| XLI | Industrials SPDR | 1,934 | 13 | 172.15 | 0.234 | 0 |
| XLY | Consumer Discretionary SPDR | 1,318 | 12 | 112.66 | 0.258 | 0 |
| XLP | Consumer Staples SPDR | 1,078 | 13 | 83.15 | 0.180 | 0 |
| XLU | Utilities SPDR | 1,004 | 15 | 43.04 | 0.194 | 0 |
| XLB | Materials SPDR | 922 | 12 | 51.72 | 0.267 | 0 |
| XLRE | Real Estate SPDR | 222 | 5 | 43.45 | 0.250 | 0 |
| XLC | Communication Services SPDR | 964 | 11 | 110.82 | 0.231 | 0 |
| IWM | Russell 2000 | 4,840 | 32 | 290.76 | 0.244 | 0 |
| SMH | Semiconductors | 6,386 | 27 | 573.74 | 0.406 | 0 |
| XBI | Biotech | 2,088 | 14 | 160.31 | 0.332 | 0 |
| KRE | Regional banks | 1,552 | 20 | 73.37 | 0.272 | 0 |
| XOP | Oil & gas E&P | 2,020 | 15 | 194.01 | 0.338 | 0 |
| TLT | 20+yr Treasury | 2,472 | 30 | 81.71 | 0.147 | 0 |
| HYG | High-yield corporate bond | 1,294 | 19 | 79.04 | 0.126 | 0 |
| EEM | Emerging markets | 2,024 | 24 | 68.61 | 0.299 | 0 |
| FXI | China large-cap | 1,384 | 22 | 34.62 | 0.252 | 0 |
| SLV | Silver | 4,854 | 27 | 60.995 | 0.496 | 0 |
| USO | Crude oil | 4,628 | 21 | 149.50 | 0.458 | 0 |
| GDX | Gold miners | 3,042 | 17 | 99.70 | 0.470 | 0 |

**All 23 passed verification; none failed and none was held back from the default
`EXTENDED_SYMBOLS`.** Zero missing-open-interest contracts on every symbol — cleaner on this
axis than SPX's own chain, which routinely has a handful. XLRE is, as the plan anticipated, the
thinnest chain (222 contracts, 5 expiries), but it is a real, currently-listed chain, not a
degenerate one, so it stays in rather than being dropped. Every median IV is comfortably under
1.0, confirming the same decimal-fraction convention as SPX/SPY/GLD/DIA (docs/schema.md's smoke
test) rather than a percent-like ETF-specific quirk.

### Dividend-yield sensitivity: one low-yield, one high-yield sector

Per this task's plan, the global `DIVIDEND_YIELD = 0.013` parameter (S&P-ish, T33's per-expiry
carry fit not yet landed) is measured, not silently ignored, for one low-yield and one
high-yield sector. XLK (Technology) and XLU (Utilities) were chosen as the two extremes of the
eleven sector SPDRs: **XLK's trailing yield is roughly 0.6–0.8 %**, **XLU's is roughly
2.8–3.2 %** (approximate, illustrative figures — this project's convention is a fixed
parameter, never fetched from a yield feed, same as `RISK_FREE_RATE`). Measured against the
**full, untrimmed live captures from 2026-09-09** (2,336 and 1,004 contracts respectively — not
the trimmed test fixtures), recomputing `compute_all(..., filter=ALL)` at the current global
default versus each symbol's approximate real yield:

| Symbol | q | Net GEX | Abs GEX | \|net\|/abs | Call wall | Put wall | Flip |
|---|---:|---:|---:|---:|---:|---:|---:|
| XLK | 0.013 (current default) | −894,105 | 224,385,272 | 0.40 % | 200.0 | 175.0 | 188.461 |
| XLK | 0.007 (approx. real yield) | −344,766 | 224,086,287 | 0.15 % | 200.0 | 175.0 | 188.399 |
| XLU | 0.013 (current default) | −27,144,886 | 177,397,167 | 15.30 % | 44.0 | 40.0 | 43.608 |
| XLU | 0.030 (approx. real yield) | −30,803,613 | 177,299,531 | 17.37 % | 44.0 | 40.0 | 43.683 |

Two different pictures, and both are informative:

- **XLK is DIA's case, not GLD's.** Its net GEX is 0.15–0.40 % of its gross absolute gamma on
  this capture — an order of magnitude below the report page's noise-dominated floor either
  way — so the ±61 % swing in the *net* number under the carry correction is a swing in a
  quantity that was never trustworthy as a directional signal to begin with. The correct
  reading of an XLK row is "noise-dominated," identically to DIA's §9 case, regardless of which
  `q` produced the number. Report it that way; do not read a sign flip here as market-moving
  when the underlying quantity was noise both times.
- **XLU is a real signal, and it moves but does not flip.** At 15.3–17.4 % of gross, XLU's net
  GEX is well clear of the noise floor under both yields, and the carry correction *increases*
  the magnitude of an already-negative (short-gamma) reading rather than flipping its sign —
  the opposite direction from DIA's flip in §9. A high-yield sector's dealer-positioning
  reading is directionally trustworthy at the current global default, but the *magnitude*
  (15.3 % vs 17.4 %) is sensitive enough that T33's per-expiry carry fit still matters for
  anything that reads the number quantitatively rather than just its sign.
- **Walls are unaffected on both symbols** (XLK 200/175, XLU 44/40, identical at both yields) —
  the same `argmax`/`argmin`-over-a-discrete-grid robustness §9 documents for GLD/DIA, not a
  new result specific to these two.

**Do not special-case per-symbol dividend yields here**, for the identical reason §9 gives for
DIA: T33's per-expiry carry fit from put-call parity is the right level to fix this at, for all
28 symbols (five core plus 23 extended) at once. A regime-board verdict (T48) reading a sector
ETF's positioning should gate on the same noise-dominated floor the report page already uses,
which is exactly what makes XLK's case safe to report today even with the carry parameter
still wrong for it.

### Storage estimate

Measured by writing the same two full 2026-09-09 captures (XLK: 2,336 contracts → 117,887
bytes; XLU: 1,004 contracts → 55,895 bytes) through the real `write_snapshot` Parquet writer:
**~50–56 bytes per contract**, consistent with the core five's own Parquet files. Summed across
all 23 verified symbols' live contract counts (51,924 contracts total on 2026-09-09), one
16:45 ET capture cycle writes approximately **51,924 × ~53 bytes ≈ 2.75 MB/day**, or
**~693 MB/year** at 252 US trading days — on top of whatever the core five's 16:20 job already
writes. Negligible against typical local disk budgets, and the free-tier constraint this
project runs under (PLAN.md's <$50/mo target) is compute and API access, not storage.
