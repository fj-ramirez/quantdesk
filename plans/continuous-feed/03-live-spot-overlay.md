# Live spot against a frozen surface (T72)

The cheapest real increment in this initiative, and the one that was missing from the original
roadmap. It is free, needs no vendor, no account and no licence, and it delivers most of what
"continuous" is actually wanted for.

## Goal

Between 15-minute captures, the distance from the current price to the flip point and the
walls updates live, while the gamma structure itself stays honestly labelled as delayed.

## The idea

Open interest is fixed for the session by construction. Implied volatility moves, but slowly
relative to price. **Spot is the fast-moving input, and spot is free** — it is not OPRA data,
it carries no entitlement fee, and `providers/yahoo.py` already fetches it for T42's daily
bars.

So: hold the whole surface frozen at the last 15-minute capture — OI, IV, every contract's
gamma, the entire 201-point gamma profile — and move only spot. That gives a live answer to
"how far am I from the flip point right now, and which side of it am I on", which is the
question a gamma dashboard is usually open for, without a single OPRA-licensed byte.

## What the user sees

On the Overview and GEX Explorer views:

- A live price marker on the by-strike and profile charts, moving between captures.
- A live readout: distance to flip point, distance to call wall, distance to put wall, in
  points and percent, and which gamma regime spot currently sits in.
- **Two freshness stamps, not one.** "Spot 14:32:05 · structure 14:15" — see the design
  decision below.

## Design decisions

**Two clocks, shown as two clocks.** This is the decision the whole task turns on. A chart
where the price marker moves smoothly invites the reading that everything on it is live. It is
not: the walls, the flip point and the profile are all up to 15 minutes old, and on a fast
move the *real* flip point has moved too. Showing one merged "last updated" stamp would be
actively misleading in exactly the conditions the user most wants this view — a fast tape.
So the two are labelled separately and the delayed half is visually subordinate (the existing
delay-badge treatment from `T19`). Carry `effective_at` for the structure, per T34, and a
plain wall-clock stamp for spot.

**Interpolate the cached profile; do not recompute.** `app.gex.engine.gamma_profile` already
produces a 201-point ±10 % grid at 0.1 % steps as part of `compute_all` at every capture —
about 5.8 M gamma evaluations for a full SPX chain, inside the 2 s budget, but far too slow to
repeat on a 5-second tick. It is currently computed and discarded (there is no profile table,
and adding one would add rows to the table `T32` is already pruning). Cache the latest
profile per (underlying, filter) **in process** at capture time and linearly interpolate net
GEX at live spot between grid points. At 0.1 % steps the interpolation error is negligible
against a figure quoted in billions.

Distance-to-level needs no profile at all — `gex_levels` already stores `flip_point`,
`call_wall`, `put_wall` and `spot` per (snapshot, filter), so that half is arithmetic on rows
that are already there.

**Polling, not streaming, and on the client's own schedule.** A 5-second `GET /api/spot/{...}`
served from a short-TTL server-side cache is enough; the vendor is polled once per interval
regardless of how many tabs are open. No WebSocket, no SSE for this — `T19`'s SSE channel
carries *structure* updates, which happen 27 times a day, and mixing a 5-second price tick
into it would make that channel chatty for no benefit.

**Degrade to the captured spot.** If the live quote fails, times out, or is stale, fall back
to the snapshot's own `spot` and say so in the badge. A frozen live marker that silently stops
tracking is worse than an honest "spot unavailable, showing 14:15 capture".

**Judgment call for the agent, named as such:** which symbol each underlying's live spot comes
from, and what its actual delay is. `^GSPC` for SPX, the ETF's own ticker for SPY/QQQ/GLD/DIA
is the obvious mapping, but **the real delay per symbol must be measured, not assumed** — see
first-contact failures. If a symbol turns out to be 15 minutes delayed from this source, it
gains nothing here and should be left showing the captured spot rather than dressed up as
live.

## Tasks

### T72 · Sonnet · T19

**Live-spot overlay against a frozen surface**

- Backend: a quote path in `providers/yahoo.py` (or a sibling) returning the latest price plus
  the vendor's own timestamp; a short-TTL cache; `GET /api/spot/{underlying}` returning price,
  vendor timestamp and a staleness flag.
- Backend: an in-process cache of the latest `gamma_profile` per (underlying, filter),
  populated at capture time, with interpolation at an arbitrary spot. Lives in
  `app/gex/store.py` or a new `app/gex/live.py` — **not** in `engine.py` or `greeks.py`, which
  invariant 1 keeps pure and which must not learn about caches.
- Frontend: live marker, distance readouts, and the two-clock freshness treatment; falls back
  cleanly when the quote is unavailable.
- Config flag `LIVE_SPOT_ENABLED`.

Acceptance: with the backend serving a fixed snapshot, feeding a changed spot moves the marker
and the distance readouts without any new capture, the structure stamp does not move, and
killing the quote source drops the view back to the captured spot with a visible reason.

## Verified facts (2026-09-11)

- `app.gex.engine.gamma_profile` exists and is called from `compute_all`; the grid is ±10 % of
  spot in 0.1 % steps → 201 points (`engine.py:199`). `ProfilePoint` is "total dealer GEX *if*
  spot were `spot`" — exactly the quantity this task interpolates.
- The profile is **not** persisted: the schema has `Snapshot`, `GexLevel`, `GexByStrike`,
  `DailyBar`, `EtfSharesOutstanding` and `Decision`, and no profile table.
- `gex_levels` stores `flip_point`, `call_wall`, `put_wall` and `spot` per (snapshot, filter),
  all nullable on purpose.
- `providers/yahoo.py` exists from T42 and already handles `^GSPC`/`^VIX`-style index symbols.
- Live spot is not OPRA-entitled data; nothing in this task touches the $50/month data budget
  or the single-user redistribution constraint.

## Likely first-contact failures

- **Assuming the free quote is actually real-time.** Yahoo's delay varies by symbol and venue
  and has changed before. Measure it: poll during a session and compare against a known live
  reference before labelling anything "live". If it is delayed, this task's premise fails for
  that symbol and the honest answer is to show the captured spot.
- `providers/yahoo.py`'s documented quirks apply here: the last bar of an in-progress session
  is partial (its `volume` is a running total, not a settled one), and the index symbols
  behave differently from the ETFs. The quote path must not reuse bar logic that assumes a
  settled bar.
- Interpolating across the profile's edges: spot can move outside the ±10 % grid built at the
  last capture on a violent day. Clamp and flag rather than extrapolating.
- Mixing the two clocks back together in a tooltip or an export after carefully separating
  them in the chart header.
- Caching the profile per process and then forgetting it is empty after a restart until the
  next capture — serve distance-to-level (which needs no profile) regardless, and degrade the
  net-GEX-at-spot readout alone.

## Out of scope

Live IV. That is the paid tier (`T21`–`T23`) and the thing this task deliberately does not
pretend to offer.
