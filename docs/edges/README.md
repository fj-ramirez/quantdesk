# Edge candidates

One file per candidate, `<slug>.md`, plus its trade journal `<slug>-trades.csv`. The process,
the gates and the kill-rule method are in `.claude/skills/edge-research/SKILL.md`. This index
holds only where each candidate stands. Results belong in the candidate's own file.

A candidate moves forward only by passing its gate. A changed rule becomes a new candidate
with its own slug and start date.

| Slug | Instruments (desk → CFD) | Stage | Next gate | Since | Source |
|---|---|---|---|---|---|
| [flip-cross](flip-cross.md) | SPY/SPX → US500, QQQ → NAS100, DIA → US30, GLD → XAUUSD | 0 → 2 (no stage 1 possible) | Demo: ≥ 50 trades over ≥ 2 months | 2026-10-08 | [read review 10-08 §4](../read-review-2026-10-08.md), T142 |
| [xau-range-rejection](xau-range-rejection.md) | GLD → XAUUSD | 0 (stage 1 possible, not run) | Historical: ≥ 100 trades on XAUUSD M1, demo-only until then | 2026-10-09 | GLD GEX + price-action read, 10-08/09 |

Stages: 0 spec frozen · 1 historical test · 2 demo forward · 3 micro live (0.01 lot) · 4 sized ·
paused (back to demo) · killed.
