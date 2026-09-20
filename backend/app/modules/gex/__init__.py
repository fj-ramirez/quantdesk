"""The GEX module: gamma exposure analysis for SPX, SPY, QQQ, GLD, DIA and the scan universe.

Everything that was `backend/app/*` before T75 -- providers, models, the pure engine, the scan
modules, jobs, Parquet storage and the ten API routers -- lives here unchanged. T75 moved it;
it did not touch a single behaviour.

`router.py` is the module's one public surface to `app/main.py`.
"""
