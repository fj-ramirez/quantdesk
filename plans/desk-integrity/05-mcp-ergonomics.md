# The connector's row shape and tool granularity

Not from the review — from the observation that a `/market-research` session spends around
twelve calls on the MCP connector. Evaluated 2026-09-21; see
[the verification document](../../docs/state-review-2026-09-21-verification.md) § *The MCP
connector*.

## Goal

Twelve calls to four or five, without weakening a single caveat.

## What the user sees

`gex_levels("QQQ")` with defaults returns **~9,000 tokens, truncated at 100 rows**, to answer
a question whose answer is the first three rows. Roughly 97% waste — and it arrives carrying a
truncation warning telling the model that any summary it writes is incomplete, at the exact
moment it had 33× more data than it needed.

## Data

No database change. Every number these tools need already exists.

## Design decisions

### 1. The notes stay. They are not the problem.

Measured: the per-tool note is ~60 tokens against ~9,000 of rows. "Every result carries its
caveats, structurally" is the best idea in this connector and the reason a smaller model on
the other end still reports the noise ceiling. **Do not trim a caveat to save tokens.** Every
saving in this file comes from rows and round trips.

### 2. `gex_levels` should do what it already claims

Its description promises "the most recent snapshot on or before `date`", singular. Its SQL
applies no such restriction — `ORDER BY captured_at DESC` capped by `limit`, so a caller gets
~33 snapshots across 3 filters. Make the behaviour match the description; put history behind
an explicit parameter.

And accept **more than one symbol**, or none meaning all. The real question is nearly always
the board, and across a 28-symbol universe the current shape is 28 calls or a drop to
`query_sql` — which is what the `market-research` skill actually teaches, bypassing the
domain tool and its caveats entirely. A tool that is inconvenient for the common case does
not merely cost calls; it costs the caveats.

### 3. Correct the `outcome` note — it is factually wrong

`gex_decisions` tells every caller:

> `outcome` null means still pending, which is different from a loss.

`outcome` is **never null**. The literals are `pending` (51), `stop` (27), `target` (13),
`untriggered` (6). A model following that note's null-check concludes nothing is pending. The
note also omits `untriggered` entirely — six decisions that never filled, which the skill
separately and correctly insists are not losses.

The real pending test is `result_r IS NULL`. This matters beyond ergonomics: **97 rows have an
`outcome` and only 40 have a `result_r`**, so the two readings differ by more than a factor of
two on every track-record number.

### 4. Two new tools, both replacing SQL the skill currently hardcodes

- **A desk-status tool.** The skill's opening instruction is "Check freshness before quoting a
  single number — non-negotiable, and first, every time", followed by hand-written SQL. One
  guaranteed `query_sql` per session, for the single thing the connector most wants enforced.
  A tool can also carry the rules the skill states in prose — weekend captures hold the
  previous session, a passed opex voids a profile rather than ageing it — beside the numbers
  they apply to.
- **A track-record tool**, per key, returning `n`, wins, mean R and standard error. This is
  the `F7d` fix: the skill hardcodes both the query and its results, and the results rot
  silently and are then quoted with full confidence. A tool leaves nothing to hardcode.

The track-record tool must compute resolved as `result_r IS NOT NULL` (decision 3) and must
return the standard error **in the same payload** as the mean, for the same structural reason
the leaderboard returns the noise ceiling beside its rows: at n=3 a mean R is not a result,
and a caller must not have to ask a second question to find that out.

**It must also derive its key list from the data, never from a hardcoded four.** `T99` is
adding a fifth key, `GAMMA_PIN`, and runs in parallel with this task. A tool that enumerates
`FADE_CALL_WALL`, `FADE_PUT_WALL`, `CONTINUATION_UP`, `CONTINUATION_DOWN` will silently drop
the new setup from the track record on the day it starts emitting — which is the exact failure
this initiative is named after. `GROUP BY key`, and let the data say what the keys are.

### 5. `gex_decisions` needs to be terminal for the common question

It returns neither `spot`, `stop`, `target`, `snapshot_id` nor `payload.thesis`, and has no
`key` filter — so any question about a level or a rationale becomes a `query_sql` follow-up.
`F2` was found that way. Widen it, and add the filter.

### 6. Optional: make the schema resource pay its own way

`query_sql` says "Read the `quantdesk://schema` resource before writing a query." Many clients
do not auto-load resources, so either it is skipped and queries are guessed, or it is read and
that is one large round trip returning every column of every table in three schemas. Folding
the relevant table's columns into `query_sql`'s **error path** — on an unknown-column or syntax
failure — pays for the schema when it is needed instead of speculatively. Judgment call; take
it only if it stays simple.

## Tasks

### T105 · Sonnet · —

Row shape. `gex_levels` returns the latest snapshot per symbol by default, accepts several
symbols or none for all, and gains an explicit history parameter; its description and its SQL
agree. `gex_decisions` gains `spot`, `stop`, `target`, `snapshot_id` and the thesis, plus a
`key` filter, and its `outcome` note is corrected to name all four literals and `result_r IS
NULL` as the pending test. No caveat is shortened.

Paths: `backend/app/mcp/server.py`, `backend/tests/test_mcp_*.py`.

### T106 · Sonnet · T105

Two tools: desk status (freshness across all three modules, with the weekend/opex rules beside
the numbers) and track record (per key: `n`, wins, mean R, standard error, resolved defined as
`result_r IS NOT NULL`, **keys derived from the data** so `T99`'s `GAMMA_PIN` appears without a
code change).

Paths: `backend/app/mcp/server.py`, `backend/tests/test_mcp_*.py`.

Chain with `T105` in one worktree — same file, same model, strict dependency.

## Verified facts

Measured 2026-09-21 against live Postgres.

- `gex_levels("QQQ")` with defaults: ~9,000 tokens, truncated at 100 rows, ~33 snapshots.
  The useful answer is 3 rows.
- `DEFAULT_ROW_LIMIT = 100`, `MAX_ROW_LIMIT = 1000`, `STATEMENT_TIMEOUT_MS = 10_000`.
- `gex.decisions`: 97 rows. `outcome` is never null — `pending` 51, `stop` 27, `target` 13,
  `untriggered` 6. `result_r` is non-null on **40**.
- `T99` is adding a fifth decision key, **`GAMMA_PIN`**, in parallel with this task. Nothing
  here may assume the key list is closed.
- Current track record, for the `T106` acceptance check: overall +0.187R, SE ±0.180, n=40.
  Per key — `FADE_CALL_WALL` +1.162R (n=7, 4 wins), `FADE_PUT_WALL` +0.133R (n=4, 1 win),
  `CONTINUATION_DOWN` +0.094R (n=26, 7 wins), `CONTINUATION_UP` −1.212R (n=3, 0 wins).
- `research_leaderboard` issues two queries per call (a trial count, then the rows). The count
  over 134k rows is milliseconds; leave it.
- The connector authenticates as `quantdesk_ro` and `assert_read_only` verifies at startup that
  the role holds no write grant. **The role is the security boundary, not the SQL check** —
  nothing in these tasks may weaken it.
- Nothing in this process may print: stdout is the transport and a stray `print` corrupts the
  protocol while looking like a client bug.

## Acceptance

1. **Measured, not estimated:** re-run the QQQ levels question and record the new token count
   beside the old ~9,000 in the `Result` section.
2. A board-wide levels question is **one** call.
3. `gex_decisions` answers "what was the thesis for decision 78" without a `query_sql`
   follow-up.
4. The track-record tool reproduces the four per-key figures in the verified facts exactly,
   and returns SE beside every mean.
5. **A key the tool has never seen appears without a code change.** Insert a fifth key into a
   fixture database and confirm it shows up — `T99` is adding `GAMMA_PIN` in parallel.
6. A `/market-research` session on QQQ is replayed end to end and the MCP call count is
   recorded. Target 4–5; **report the real number whatever it is.**
7. Every existing caveat is still present verbatim — diff the notes.
8. `uv run pytest` and `uv run ruff check .` clean.

## Likely first-contact failures

- **Defining resolved as `outcome IS NOT NULL`**, which returns 97 instead of 40 and silently
  changes every number. This is the single most likely error in `T106`.
- **Hardcoding the four current decision keys.** `T99` adds `GAMMA_PIN` while this task is in
  flight; a hardcoded list drops it silently.
- **Trimming the notes to save tokens.** Decision 1.
- **Adding a `print` for debugging.** It corrupts the stdio transport.
- Loosening the read-only role, the statement timeout or the row cap in the name of
  convenience.
- Changing a tool's name, breaking the `market-research` skill without updating it (`T107`).
- Making multi-symbol `gex_levels` emit one truncation warning for the whole result when only
  one symbol was truncated.
- Forgetting that `research_paper`'s `LEFT JOIN` is deliberate — a candidate promoted since the
  last cycle has no score row and must still appear.

## Out of scope

- The terminal and research tools' shapes. They are called once or twice per session and are
  not where the cost is.
- Any change to the noise ceiling, the point-in-time rule or the null marker.
- A write path of any kind.
