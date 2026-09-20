"""EdgeLab: automated trading-edge search, ported into quantdesk as `modules/research` (T77).

Formerly `projects/research`, a standalone repo with its own SQLite registry and its own
nightly runner. The science did not move -- `backtest.py`, `strategies.py`, `validation.py`,
`robustness.py`, `xs.py` and `paper.py` are the originals, byte for byte where nothing forced a
change. What moved is storage and scheduling: the registry is the `research` Postgres schema
(see `registry.py`), and the search runs in the `research-search` worker container on the same
APScheduler shape GEX uses.

**EdgeLab's honesty rules are the product**, and they survive the port intact: the noise
ceiling (what the best OOS Sharpe would look like under pure luck, given how many trials have
been run), the pessimistic doubled-cost robustness gate, the walk-forward consistency gate, and
the futures roll-gap caveat. A leaderboard without the noise ceiling is worse than no
leaderboard, because it looks authoritative. See `plans/quantdesk/02-research-module.md`.
"""
