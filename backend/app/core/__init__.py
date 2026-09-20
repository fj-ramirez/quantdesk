"""Shared by every module: settings, the SQLAlchemy engine/session plumbing, and the
Postgres schema names (T75).

Deliberately tiny, and kept that way. `plans/quantdesk/README.md` is explicit that the real
shared core -- bars, symbols, the market calendar, the provider ABCs -- is the *successor*
initiative, to be designed once there are three real consumers rather than one and two
guesses. Anything added here before then is a guess. Nothing in this package may import from
`app.modules.*`.
"""
