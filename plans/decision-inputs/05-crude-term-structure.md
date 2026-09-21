# Crude term structure (T95)

The eval's cleanest unresolved question: backwardation versus contango is what separates a
physical supply squeeze from speculative froth, and nothing stored can answer it.

## Goal

The desk can tell whether a crude move is backed by the curve or not, and XOP/USO calls stop
resting on spot alone.

## What the user sees

A contango/backwardation reading beside the energy names, and the front-spread on the change
board with its own z-score like every other series.

## Data

What exists: `cmdty.wti` — 6,012 observations to 2026-09-15, a **spot/front-month price and
nothing else**. There is no second contract, so no spread, so no curve shape.

What the question needs: at minimum the front two contract months, ideally the first six, at
daily frequency. That is the whole task — everything downstream is arithmetic.

The eval also flags, correctly, that `fx.usd.broad → cmdty.wti` is currently the graph's one
sign conflict (corr +0.4056 against an expected −1, 87.57th percentile). A dollar-up,
oil-up regime is exactly the situation where curve shape distinguishes a supply story from a
liquidity one, so this task and that conflict are the same question from two sides.

## Design decisions

**The source survey is the task, and it comes first.** CME's own settlements are already
established as off-limits for automated use — `cli.py:229` records that their Data Terms of Use
prohibit it, and it is the reason T96 exists. Do not rediscover this. Candidate keyless or
cheap sources worth checking, in the order they are likely to work: **EIA's open API** (spot
and some futures series, no cost, no terms problem), **FRED** (already an adapter here, carries
several energy series), and the front-month continuous contracts some free providers publish.
The deliverable of the first half of this task is a written answer to "which source, at what
cost, under what terms" — the same shape as `PLAN.md`'s original data-source survey.

**Stop if the survey comes back empty.** A curve that cannot be sourced legally and freely is
not a thing to approximate from spot. Record the negative result and close the task; a fabricated
term structure is worse than none, because it looks like evidence.

**Store the contracts, derive the spread.** The M1-M2 spread is one subtraction, and the
terminal already has a `derive` step for exactly this class of series. Storing the spread
without the legs would mean a later question about the 6-month shape needs a re-ingest.

**Point-in-time rules apply (invariant 10).** Futures settlements get revised; a revision adds
a row with a new `as_of`, never an overwrite. Contract rolls are the sharp edge here — see the
failures below.

## Tasks

## T95 · Sonnet · —

**First:** survey the sources above and write the answer into this file under a *Source
survey* heading — which one, what it carries, what it costs, what its terms permit. If none
qualifies, that is the result and the task ends there.

**Then, if one does:** a terminal adapter for it, ingesting at least the front two contract
months into `cmdty.wti.m1` / `cmdty.wti.m2` (or the naming the existing scheme implies), with
the M1-M2 spread as a derived series in the `derive` step.

Surface the spread on the change board, and add the sign of the spread to whatever the energy
names read.

## Verified facts

Measured 2026-09-21:

* `cmdty.wti` holds 6,012 observations to 2026-09-15 and is a single series — there is no
  second contract month anywhere in `terminal.observations`.
* `fx.usd.broad → cmdty.wti` is currently the graph's only `sign_conflict = yes`: corr +0.4056,
  expected −1, 87.57th percentile, significant, 250 observations.
* CME settlements are already known to be unavailable for automated use; do not re-survey them.
* `terminal.observations` is 219,107 rows across 75 series, so a handful of contract series is
  immaterial to storage.

## Acceptance

* A written source survey in this file, with a decision — including the case where the decision
  is "none, and here is why".
* If a source was found: the legs and the derived spread present and updating nightly, the
  spread on the change board with a z-score, and the sign reproducing a hand-check against a
  published curve on one date.
* The `cmdty.wti` series itself is unchanged — this task adds, it does not redefine.

## Likely first-contact failures

* **The roll.** A continuous front-month series has a discontinuity every month, and a naive
  spread across a roll date produces a spike that is pure artifact. EdgeLab already carries a
  futures roll-gap caveat (invariant 9) for exactly this reason — read it before designing the
  series rather than after seeing the spike.
* **Mixing WTI and Brent.** The eval itself caught an implausible Brent 130.8 against WTI 107
  by eye. Two grades in one series would be worse than either alone.
* **Inferring the curve from spot plus storage costs.** That is a model, not an observation, and
  it will be read as an observation.
* **Assuming FRED's energy series are futures.** Several are spot or averages; check the series
  definition, not the title.

## Out of scope

Other commodity curves, options on crude, inventory data (EIA weeklies are a separate and
larger question), and resolving the dollar/oil sign conflict — this task supplies evidence for
that, it does not settle it.
