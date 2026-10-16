# Edge candidates

One file per candidate, `<slug>.md`, plus its trade journal `<slug>-trades.csv`. The process,
the gates and the kill-rule method are in `.claude/skills/edge-research/SKILL.md`. This index
holds only where each candidate stands. Results belong in the candidate's own file.

A candidate moves forward only by passing its gate. A changed rule becomes a new candidate
with its own slug and start date.

| Slug | Instruments (desk → CFD) | Stage | Next gate | Since | Source |
|---|---|---|---|---|---|
| [flip-cross](flip-cross.md) (+ `_0DTE` variant) | SPY/SPX → US500, QQQ → NAS100, DIA → US30, GLD → XAUUSD, IWM → US2000 | 0 → 2 (no stage 1 possible) | Demo: ≥ 50 trades over ≥ 2 months | 2026-10-08 | [read review 10-08 §4](../read-review-2026-10-08.md), T142 |
| [wall-bounce](wall-bounce.md) (+ `_0DTE` variant) | SPY/SPX → US500, QQQ → NAS100, DIA → US30, GLD → XAUUSD, IWM → US2000 | 0 → 2 (no stage 1 possible); exit frozen 2026-10-14 | Executor class (T163); then demo: ≥ 50 trades over ≥ 2 months | 2026-10-12 | the user's alert request, T153 |
| [wall-break](wall-break.md) (+ `_0DTE` variant) | SPY/SPX, QQQ, DIA, GLD, IWM (measured on the CFD path) | 0, **logged and alerted, not traded** (no executor class) | First read at ≥ 30 sessions with events | 2026-10-14 | a reel's "break → runs to the next level" claim, T164 |
| [gold-asia-drift](gold-asia-drift.md) | — → XAUUSD | 1 passed → 2 | Demo: ≥ 30 trades, measured 18:00 spread ≤ $0.48 | 2026-10-09 | documented-effects test 10-09, `scripts/edges/` |
| [nas-mean-reversion](nas-mean-reversion.md) | — → NAS100.fs | 1 passed (independent 2000–19) → 2 | Executor class `nas_mr` written 2026-10-15, parity with the backtest exact; enable it, then demo: ≥ 30 trades | 2026-10-09 | leads #334/#378/#419 → composite #428 |
| [index-hold-sma200](index-hold-sma200.md) | — → NAS100.fs, S&P.fs | risk premium (no edge gate) | Executor class (T152); demo 0.01 lot; verify the 2026-12-14 roll adjustment | 2026-10-09 | pass 3 summary, search-log.md |
| [xau-range-rejection](xau-range-rejection.md) | GLD → XAUUSD | **stage 1 failed** (2026-10-14, T149, #532): +0.10R ± 0.10 on 83, OOS −0.14R | None for this rule | 2026-10-09 | GLD GEX + price-action read, 10-08/09 |
| [rty-zscore-dip](rty-zscore-dip.md) | RTY=F → US2000 | **stage 1 failed** (2026-10-15, #535): −0.091% per trade, t −1.18 on 339, gross ≈ 0 | None for this rule | 2026-10-15 | EdgeLab family read: RTY=F 1h `zscore_meanrev` 78% positive OOS |
| [gamma-conditioned-walls](gamma-conditioned-walls.md) | as wall-bounce / wall-break | 0, a filter on those keys | Stage 1 inside T167 (T166 history); forward read `gex_level_events(by_gamma=True)` | 2026-10-16 | directional-signals survey, backlog 49 |
| [turn-of-month](turn-of-month.md) | ^GSPC/^NDX → S&P.fs, NAS100.fs | **lead** (#539–540: t 2.5 / 2.7 on 1990–2019, bar 3.55) | None unless the user accepts it as a bias; see [directional-signals-2026-10-16](directional-signals-2026-10-16.md) | 2026-10-16 | backlog 46 |
| [rod-momentum](rod-momentum.md) | — → 3 index CFDs | **stage 1 failed** (#536–538): −0.03 to −0.05% per trade, gross ≈ 0 | None for this rule | 2026-10-16 | backlog 45 |
| [month-end-rebalancing](month-end-rebalancing.md) | — → S&P.fs, NAS100.fs | **stage 1 failed** (#541–542): Sharpe 0.10 / 0.00 vs B&H 0.66 / 0.79 | None for this rule | 2026-10-16 | backlog 47 |
| [fomc-cycle](fomc-cycle.md) | — → S&P.fs, NAS100.fs | **stage 1 failed** (#543–544): gone after publication | None for this rule | 2026-10-16 | backlog 48 |
| [eurusd-burst-fade](eurusd-burst-fade.md) | — → EURUSD | **stage 1 failed** (2026-10-11, #429–430): the frozen z-fade loses −2.0 pips per trade, 0/8 years | None for this rule. The provider's entry selector is still unexplained; it becomes a test again only as a new, pre-declared rule | 2026-10-10 | the user's copy account: provider "the holy grail" |

**Leads** (they cleared everything but the t bar and are **not** candidates; see
[search-log.md](search-log.md)): RSI(2) pullback on S&P.fs / NAS100.fs (#334–336). Its sign replicated on 2000–19 (#356–357),
but t is below the bar. Pre-FOMC drift (#524–525) cleared t 3.1 on 1994–2019, but only in the paper's own
sample; after publication it decayed to t ≈ 1, so it is not demo-tested.

**Size:** every candidate trades 0.01 lot from demo through micro live. The $100/month desk cost
is a stage-4 question, not a gate.

Stages: 0 spec frozen · 1 historical test · 2 demo forward · 3 micro live (0.01 lot) · 4 sized ·
paused (back to demo) · killed.
