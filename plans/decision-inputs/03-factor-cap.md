# Factor cap: stop emitting one trade seventeen times (T93)

The eval's strongest point, and the one it makes against itself: *"my most important risk note
— 'these eight are one trade' — was an assertion, not a measurement."*

## Goal

The decision path knows how correlated its own candidates are, and can refuse to emit a book
that is one bet wearing seventeen tickers.

## What the user sees

A decision set that reports its factor structure — how many independent bets it actually
contains — and caps exposure per factor instead of ranking each candidate as though the others
did not exist.

## Data

Everything needed is already stored. `gex.daily_bars` holds **157,152 bars across 125 symbols**
with five years of history (1,262 bars per symbol for the liquid names), current to today. No
new source, no new ingestion, no vendor.

The existing decision path is `scan/decisions.py` with `api/decisions.py` over it, and
`_STATUS_RANK = {"active": 0, "watch": 1, "rejected": 2}` is the whole of its current notion of
precedence. Nothing in it looks at the relationship between two candidates.

## Design decisions

**Rolling correlation first; PCA only if the correlation matrix does not answer it.** A 60-day
rolling correlation over the candidate set is interpretable, cheap, and directly answers "are
these the same trade". A principal-components decomposition answers a subtly different and
more ambitious question — how many latent factors span the universe — and buys precision the
desk cannot yet act on. Ship the matrix; keep PCA as a named follow-on rather than a stretch
goal inside this task.

**The cap is a constraint on the emitted set, not a re-ranking.** Ranking by score and then
dropping correlated duplicates is greedy and order-dependent, but it is explainable: "XLE was
dropped because it is 0.91-correlated with XOP, which scored higher." A portfolio optimizer
would produce a better book and an unexplainable one, and for a single-user desk that has to
trust its own output, explainability wins.

**The threshold and the cap are configuration, not constants in the algorithm.** The right
correlation cut-off is an opinion about how much concentration is acceptable, and it will be
revised after the first week of looking at the output. It belongs beside the other tunables,
not buried in a function.

**Correlate returns, not prices.** Two trending price series correlate near 1 whether or not
they move together day to day. This is the single easiest way to get a result that looks
authoritative and measures nothing.

**This is pure computation over a frame, and it stays pure.** No HTTP, no database, no
filesystem inside the computation itself — the same discipline invariant 1 imposes on the GEX
engine, applied here because the same reasons hold: it makes the thing testable against
hand-computed cases.

## Tasks

## T93 · Opus · —

New pure module under `app/modules/gex/scan/` computing, over a set of candidate symbols and
their daily bars: the pairwise rolling-return correlation matrix at a configurable window, a
per-candidate maximum correlation against higher-ranked candidates, and the effective number of
independent bets in the set.

Then a selection pass in `scan/decisions.py` that walks candidates in score order and marks
each one whose correlation to an already-accepted candidate exceeds the threshold — marked and
explained, carrying the name of the candidate it duplicates, never silently dropped.

Surface the factor summary on the decisions endpoint so the count of independent bets is
visible beside the set, and the reason on each suppressed row.

Tests against hand-computed cases: two identical series correlate 1.0 and the second is
suppressed; two independent series both survive; a candidate with insufficient overlapping
history is `NaN` and is **not** suppressed on missing data.

## Verified facts

Measured 2026-09-21:

* 125 symbols, 157,152 daily bars, 1,262 per liquid symbol, current to today. No new data is
  needed for any part of this.
* `scan/decisions.py` has no cross-candidate logic of any kind today.
* `scan/indicators.py` already establishes the pure-function-over-a-frame pattern this should
  follow.

## Acceptance

* Suite green including the hand-computed cases.
* Run against the four trades in the eval: the tool either confirms they are one trade or shows
  they are not, with a number. Either outcome is a pass; the point is that it is measured.
* A candidate suppressed for correlation names what it duplicates, in the API response.
* Missing or short history never causes a suppression.

## Likely first-contact failures

* **Correlating prices instead of returns.** See the design decision; it is the most common way
  this goes quietly wrong.
* **Suppressing on `NaN`.** A candidate with no overlapping history has an undefined
  correlation, not a high one. Comparison against `NaN` is `False` in most paths and `True` in
  some — assert it.
* **Symbol sets that do not overlap in time.** Correlation over a 60-day window needs 60
  overlapping days, not 60 days each. Align first, then measure, and report the overlap.
* **Making the cap silent.** A decision set that quietly shrank is worse than one that did not
  shrink, because the reason is unrecoverable at the point of reading it.

## Out of scope

PCA and factor attribution; position sizing and vol targeting (EdgeLab has its own); applying
the cap to anything other than the decisions path.

---

## Result — T93

**Done 2026-09-21.** 1,154 backend tests green (27 added), 403 frontend green, both linters
clean.

`app/modules/gex/scan/factors.py`, pure on the same contract as the engine: `return_frame`,
`correlation_matrix`, `effective_bets`, `mean_correlation`, `cap_candidates` and a `summarize`
that composes them. `api/decisions.py` calls it with the bars `build_regime_rows` already
fetched, so the measurement costs no extra read.

Every design decision in this file survived implementation. Three things are worth recording
because they are *not* in the spec above.

**Side is part of the question.** Two names correlated +0.95 are the same trade only when
traded the same way; long XLE against short XOP at that correlation is closer to a hedge, and
suppressing a leg of it would be exactly wrong. So the comparison is on the **side-adjusted**
correlation, `corr * sign(a) * sign(b)`, and only a positive result above the threshold counts
as duplication. An unrecognised side yields sign `0`, which can never duplicate anything —
failing towards keeping a row, because an extra row costs a line on a screen and a wrongly
removed one costs a trade the desk never sees.

**Two opportunities on one symbol are never duplicates of each other.** A symbol between its
two walls legitimately carries a short-at-the-call-wall and a long-at-the-put-wall row — the
same range read from both ends, per `scan/decisions.py`'s own docstring. They self-correlate
1.0, so the cap compares across symbols only.

**`rejected` rows are not candidates for the cap.** They are not trades being offered and
already carry a `rejection_reason`; marking them duplicates too would be noise on rows nobody
will take.

**Structural deviation from the spec, deliberate:** the selection pass lives in the new
`factors.py`, not in `scan/decisions.py` as this file said. `decisions.py` is per-symbol by
construction — `decide()` takes one `RegimeRow` — and a cross-candidate cap is a portfolio
concern. Keeping the correlation maths and the pass that consumes it in one module leaves each
file one concept; the API composes them.

`effective_bets` uses the equal-weight portfolio identity `n / (1 + (n-1) * rho_bar)` rather
than an eigendecomposition: all correlations 1 gives exactly 1.0, all 0 gives exactly `n`, and
it is checkable by hand. PCA stays the named follow-on. The guard against a non-positive
denominator is real rather than decorative — a genuine PSD correlation matrix cannot produce
one, but a pairwise-complete estimate over uneven overlaps is not guaranteed PSD.

The three tests this file asked for all pass, plus twenty-four more: the returns-not-prices
case is pinned by a fixture whose two series have price correlation > 0.9 and return
correlation < 0.4, and there is an end-to-end API test that seeds two identical symbols and
asserts one survives, the other is marked, and `independent_bets` reads exactly 1.0.

**One caveat on the frontend fixture.** `mocks/fixtures/scan/decisions.json` is a byte-for-byte
recording of a live response and predates this field set, so the new fields were added by hand
with `mean_correlation` and `independent_bets` at `null`. `null` is a real value here ("not
measurable"), not a placeholder — inventing a correlation for returns that were never recorded
would put a fabricated number in a file whose contract is that its numbers are real. Noted in
that directory's README; a re-recording will replace them.

**No UI yet.** The data and the types are in place; no component renders the factor summary or
the suppression mark. Same shape of follow-on as T92's missing column.
