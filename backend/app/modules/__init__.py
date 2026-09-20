"""The umbrella app's modules: one subpackage per selectable application.

`gex` is the only one that exists at the end of T75. `research` (T77) and `terminal` (T79)
land as siblings, each with the same shape -- `api/` routers behind one `router.py`, its own
models and ingestion, and a worker in `app/workers/` for anything clock-bound.

Modules do not discover or register themselves: `app/main.py` imports each `router.py` by
name. With three modules a visible list of three imports beats a plugin mechanism, and a
module that fails to import is then an `ImportError` at boot rather than a route that is
silently absent from a running app.
"""
