"""Configuration loading and paths for the research module (T77).

The original resolved everything from the standalone repo's root
(`PROJECT_ROOT = parents[2]`, then `data/`, `results/`, `reports/`, `logs/` beside it). Inside
quantdesk there is no such root: the code lives in the image and the data lives on a bind
mount, so every path now hangs off `settings.DATA_DIR` -- the same directory GEX writes its
Parquet tree into, and the one thing in the stack that is deliberately visible on the host.

`RESULTS_DIR` is gone rather than repointed. It existed to hold `registry.db`, and T77's whole
premise is that the registry is Postgres now; keeping a name that resolves to a plausible place
to put a SQLite file is exactly the invitation `registry.py` refuses (see its no-fallback rule).

`config/research.yaml` stays YAML and stays in the image rather than becoming environment
variables: it is a search budget and a cost model -- symbols, timeframes, fee assumptions,
promotion gates -- not deployment configuration. Editing it is a research decision, and it
wants to be reviewable as a diff.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.core.config import settings

__all__ = [
    "CONFIG_PATH",
    "DATA_DIR",
    "LOGS_DIR",
    "REPORTS_DIR",
    "ensure_dirs",
    "load_config",
]

#: Everything this module writes lives under one directory inside `DATA_DIR`, so the research
#: module's footprint on the host is a single folder that can be backed up or moved on its own
#: -- and so it can never collide with the GEX Parquet tree beside it.
DATA_DIR = Path(settings.DATA_DIR) / "research"

#: OHLCV parquet moved here as-is. Deliberately still parquet and not Postgres: invariant 5
#: already draws this line, and the backtester reads whole series into pandas and never queries
#: a single bar, so loading 33 MB of it into the database would buy nothing.
OHLCV_DIR = DATA_DIR / "ohlcv"

#: `--report-only` still writes `leaderboard.html` and `candidates.html` here. Kept because it
#: costs nothing, works when the stack is down, and is the fallback while T78's page is built.
REPORTS_DIR = DATA_DIR / "reports"

LOGS_DIR = DATA_DIR / "logs"

#: Shipped in the image, next to the application, because it is code-adjacent configuration
#: that changes with the research rather than with the deployment.
CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "research.yaml"


def load_config(path: str | Path | None = None) -> dict:
    with open(Path(path) if path else CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dirs() -> None:
    for d in (DATA_DIR, OHLCV_DIR, REPORTS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
