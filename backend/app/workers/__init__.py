"""Background-work entrypoints, one process each (T75).

The split in this application is **request path vs. background work**, not module vs. module:
one API container serves every module's routes, and anything clock-bound or CPU-bound gets
its own container running the same image with a different command. `app/main.py` therefore
starts nothing -- see `gex_capture.py` for what used to live in its lifespan and why it left.
"""
