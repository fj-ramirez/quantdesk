"""Source adapters. Each fetches from one provider and yields normalized
Observations; none of them writes to the store (spec 1.2)."""

from .base import Adapter, BaseAdapter
from .cboe import CboeAdapter
from .cftc import CftcAdapter
from .cme import CmeFileAdapter
from .fred import FredAdapter
from .treasury import TreasuryAdapter

# Network adapters, driven by `xactx ingest`. CmeFileAdapter is deliberately
# absent: it reads a local file rather than fetching (see adapters/cme.py).
ADAPTERS = {
    "fred": FredAdapter,
    "treasury": TreasuryAdapter,
    "cboe": CboeAdapter,
    "cftc": CftcAdapter,
}

__all__ = [
    "ADAPTERS",
    "Adapter",
    "BaseAdapter",
    "CboeAdapter",
    "CftcAdapter",
    "CmeFileAdapter",
    "FredAdapter",
    "TreasuryAdapter",
]
