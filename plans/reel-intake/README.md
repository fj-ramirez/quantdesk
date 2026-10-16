# Reel intake — investing claims from social media into a review queue (2026-10-14)

Three tasks, `T160`–`T162`. Approved in session 2026-10-14.

## Why

The user sends reels, links and notes to a private Telegram channel. A separate laptop tool,
`projects/reeler` (its README is the plan for that side), transcribes and classifies them in
batches, and turns everything that isn't investing (recipes, things to watch, learning) into
markdown lists. **This plan is only the desk's end:** items classified as investing arrive here
as structured claims, get point-in-time sanity checks against desk data, and wait in a queue
for a person to judge them.

## The rules

1. **A reel claim is a hypothesis, never a signal.** Accepting one means "worth testing" and
   leads to the edge-research skill. Nothing here reaches `broker.order_intents` or the
   executor (invariant 11).
2. **Nothing is backtested automatically.** Every EdgeLab run adds a trial and raises the noise
   ceiling for everything else (invariant 9). Testing a claim is a deliberate step.
3. **Append-only.** Items, claims, check results and reviews add rows; a re-check is a new row
   carrying its own `checks_version`. Same reason as signal-alerts rule 4: a verdict is evidence
   only if it can't be rewritten.
4. **Checks are point-in-time, at the reel's `posted_at`.** A claim made in March is judged
   against what the desk knew in March (the terminal's `as_of` rule). A check whose data is
   missing returns `·`, not a fail.
5. **The LLM extracts and the desk judges.** reeler's classifier produces the claims; whether
   a level or a price makes sense is decided by code in this repo, against this repo's data.

## The contract (reeler → desk)

`POST /api/intake/items`, header `X-Intake-Token: $INTAKE_TOKEN`. It is the first write
endpoint fed from outside the stack, so it gets a shared secret even on the LAN. Idempotent on
`source.key`: posting the same item again returns the existing id and writes nothing.

```json
{
  "source": {"key": "tg:<channel_id>:<message_id>", "url": "...", "platform": "instagram",
             "author": "...", "posted_at": "2026-10-01T14:00:00Z", "received_at": "..."},
  "transcript": "...", "caption": "...", "lang": "es",
  "classifier": {"model": "claude-haiku-5-5", "prompt_version": "v1", "confidence": 0.93},
  "summary": "...",
  "red_flags": ["guaranteed_return", "course_pitch", "no_horizon", "hindsight"],
  "claims": [{
    "instrument_raw": "el oro", "instrument": "GLD",
    "direction": "long", "horizon_text": "esta semana", "horizon_days": 5,
    "levels": [{"kind": "target", "value": 2750.0, "unit": "price"}],
    "rationale": "...", "testable": true
  }]
}
```

`instrument` is reeler's best mapping to a desk symbol, or `null`. The desk re-maps it, and the
raw text is kept so a bad mapping can be found later. `posted_at` may be `null` when yt-dlp
cannot tell. In that case the checks run at `received_at`, and the check row says so.

## Tasks

### T160 · Sonnet · —
**The `intake` schema and the write endpoint.** New module `modules/intake/` and schema
`intake` (invariant 8: declared on the module's `Base`; added to `core/schemas.SCHEMAS`, which
also gives the MCP read-only role its grants). Tables: `items`, `claims`, `checks`, `reviews`,
all append-only. `POST /api/intake/items` (contract above, token from `INTAKE_TOKEN`), plus
`GET /api/intake/items?status=` and `GET /api/intake/items/{id}`. Acceptance: posting twice
gives one row; a wrong or missing token gives 401; the migration runs on a copy of prod.

### T161 · Opus · T160
**Point-in-time desk checks.** These run synchronously inside the POST: they are cheap reads and
belong to the request path, not to a background job (invariant 7). Each one returns
pass/fail/`·` with a reason:
- **universe**: the symbol is one the desk covers, or a CFD on the Axi list;
- **price sanity**: each `price` level is within a plausible band of the price at `posted_at`
  (desk bars / terminal series; the band scales with the symbol's realised volatility, not a
  fixed %);
- **structure**: distance from a quoted level to the nearest GEX wall or flip on that date;
  whether the regime the claim states matches the terminal regime as of then;
- **testability**: direction + horizon + at least one level.
Acceptance: fixtures for a sane claim, a wrong-by-10× price, an unknown symbol and missing data
(which must give `·`, not fail); `checks_version` stored on every row.

### T162 · Sonnet · T160, T161
**Review queue: page, endpoint, MCP.** `/intake` in the frontend shows transcript, claims,
check badges and red flags, with accept / reject / "→ edge-research" plus a note. The verdict
goes through `POST /api/intake/items/{id}/reviews`. Add the read-only MCP tool `intake_queue`
(pending items with their checks) so the market-research and edge-research skills can see the
queue. Acceptance: a reviewed item leaves the pending list, and its earlier reviews stay
readable.

### Later, not filed
Score each accepted or rejected claim against what the market did over its horizon, per
author, the way `gex_track_record` scores the desk. The data is already there, because claims
carry `posted_at` and levels.

## Likely first-contact failures

- `posted_at` is a calendar day with no time for some platforms; checks at 00:00 UTC would read
  the previous session. Treat a date-only `posted_at` as that day's close.
- reeler maps "el oro" to XAUUSD or to GLD inconsistently. The desk re-mapping in T161 is the
  authority; log disagreements.
- The homeserver API must be reachable from the laptop; if it is published only on the docker
  network, the laptop sink fails. Confirm the port is exposed in `compose.prod.yaml`.

## Result

(empty until T160 lands)
