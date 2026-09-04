"""Parquet writer/reader for raw chain snapshots (PLAN.md §2).

One :class:`~app.models.chain.ChainSnapshot` == one Parquet file: one row per
:class:`~app.models.chain.OptionContract`, with the snapshot-level fields (``underlying``,
``spot``, ``captured_at``, ``source``, ``delayed_minutes``) written once into the Parquet file
*metadata* rather than duplicated down every row — at ~28,650 rows for a full SPX chain that
duplication would be pure waste with no upside, since none of it varies per contract.

Layout: ``DATA_DIR/chains/{underlying}/{YYYY}/{MM}/{encoded captured_at}.parquet``.

Filename encoding
------------------

The task-level shorthand for the filename is "captured_at_iso", but a literal ISO-8601
string (``2026-09-04T18:05:33+00:00``) is not a legal Windows filename: ``:`` is reserved.
:func:`_encode_captured_at` instead uses the ISO-8601 *basic* form with no separators
(``20260904T180533000000Z``), zero-padded to a fixed width down to the microsecond. Fixed
width with the most-significant field first means plain lexicographic string sort equals
chronological order, which is what makes ``ls`` / glob-and-sort over a symbol's directory a
correct way to walk history without touching file metadata. Microsecond precision (rather than
just seconds) avoids two snapshots ever colliding on a filename if a symbol is ever captured
faster than once a second (e.g. a backfill script or a retried manual capture) — a collision
would silently overwrite a prior snapshot's file on disk while a second, duplicate index row
still pointed at it.

:func:`read_snapshot` never parses the filename — the encoded timestamp is only ever a sort
key and a human hint at what's in a directory listing. Every field needed to reconstruct the
``ChainSnapshot`` (including the *authoritative* ``captured_at``) comes from the embedded
metadata, so a renamed or relocated file still reads back correctly.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq

from app.config import settings
from app.models.chain import (
    ChainSnapshot,
    OptionContract,
    Right,
    Settlement,
    Underlying,
)

__all__ = ["read_snapshot", "write_snapshot"]

#: Key under which the snapshot-level JSON blob is stored in the Parquet file's key-value
#: metadata. Namespaced so it can't collide with metadata pyarrow/pandas add on their own.
_METADATA_KEY: Final = b"gex.snapshot_meta"

#: One row per contract. Every optional field gets an explicit nullable pyarrow type rather
#: than letting pyarrow infer one from the Python values: inference on a column that happens
#: to be all-``None`` in a given snapshot (rare Greeks, say) would pick `null` type, which
#: cannot round-trip against a schema-checked read. Explicit types also keep `None` (unknown)
#: from ever collapsing into `0`/`NaN` (a real zero) — see the module docstring in
#: `app/models/chain.py` on why that distinction has to survive storage intact.
_CONTRACT_SCHEMA: Final = pa.schema(
    [
        pa.field("occ_symbol", pa.string(), nullable=False),
        pa.field("root", pa.string(), nullable=False),
        pa.field("underlying", pa.string(), nullable=False),
        pa.field("expiry", pa.date32(), nullable=False),
        pa.field("settlement", pa.string(), nullable=False),
        pa.field("strike", pa.float64(), nullable=False),
        pa.field("right", pa.string(), nullable=False),
        pa.field("bid", pa.float64(), nullable=True),
        pa.field("ask", pa.float64(), nullable=True),
        pa.field("last", pa.float64(), nullable=True),
        pa.field("volume", pa.int64(), nullable=True),
        pa.field("open_interest", pa.int64(), nullable=True),
        pa.field("iv", pa.float64(), nullable=True),
        pa.field("delta", pa.float64(), nullable=True),
        pa.field("gamma", pa.float64(), nullable=True),
        pa.field("vega", pa.float64(), nullable=True),
        pa.field("theta", pa.float64(), nullable=True),
        pa.field("multiplier", pa.int64(), nullable=False),
        pa.field("last_trade_time", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
)

_COLUMN_NAMES: Final = tuple(f.name for f in _CONTRACT_SCHEMA)


def _encode_captured_at(captured_at: dt.datetime) -> str:
    """Filesystem-safe, chronologically-sortable encoding of a tz-aware UTC instant.

    ``captured_at`` is guaranteed UTC and aware by `ChainSnapshot`'s own validator, so this
    only needs to format it, not normalize it.
    """
    return captured_at.strftime("%Y%m%dT%H%M%S") + f"{captured_at.microsecond:06d}Z"


def _snapshot_dir(data_dir: Path, snapshot: ChainSnapshot) -> Path:
    c = snapshot.captured_at
    return data_dir / "chains" / snapshot.underlying.value / f"{c:%Y}" / f"{c:%m}"


def write_snapshot(snapshot: ChainSnapshot, data_dir: str | Path | None = None) -> Path:
    """Write one snapshot to a Parquet file and return the path written.

    Args:
        snapshot: The chain to persist. Its own validators already guarantee
            ``captured_at`` is tz-aware UTC and every contract's ``underlying`` matches.
        data_dir: Root data directory. Defaults to ``settings.DATA_DIR`` so production code
            never has to pass it, while tests can point at a temp directory.

    Returns:
        The path of the written ``.parquet`` file. Writing the same snapshot (same
        underlying + same ``captured_at``, to microsecond precision) twice overwrites the
        prior file at the same path — callers that care about that (T05's manual capture
        endpoint, a backfill re-run) should treat it as an upsert, not append.
    """
    base = Path(data_dir) if data_dir is not None else Path(settings.DATA_DIR)
    directory = _snapshot_dir(base, snapshot)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_encode_captured_at(snapshot.captured_at)}.parquet"

    columns: dict[str, list] = {name: [] for name in _COLUMN_NAMES}
    for contract in snapshot.contracts:
        columns["occ_symbol"].append(contract.occ_symbol)
        columns["root"].append(contract.root)
        columns["underlying"].append(contract.underlying.value)
        columns["expiry"].append(contract.expiry)
        columns["settlement"].append(contract.settlement.value)
        columns["strike"].append(contract.strike)
        columns["right"].append(contract.right.value)
        columns["bid"].append(contract.bid)
        columns["ask"].append(contract.ask)
        columns["last"].append(contract.last)
        columns["volume"].append(contract.volume)
        columns["open_interest"].append(contract.open_interest)
        columns["iv"].append(contract.iv)
        columns["delta"].append(contract.delta)
        columns["gamma"].append(contract.gamma)
        columns["vega"].append(contract.vega)
        columns["theta"].append(contract.theta)
        columns["multiplier"].append(contract.multiplier)
        columns["last_trade_time"].append(contract.last_trade_time)

    arrays = [pa.array(columns[field.name], type=field.type) for field in _CONTRACT_SCHEMA]
    table = pa.Table.from_arrays(arrays, schema=_CONTRACT_SCHEMA)

    meta = {
        "underlying": snapshot.underlying.value,
        "spot": snapshot.spot,
        "captured_at": snapshot.captured_at.isoformat(),
        "source": snapshot.source,
        "delayed_minutes": snapshot.delayed_minutes,
    }
    table = table.replace_schema_metadata({_METADATA_KEY: json.dumps(meta).encode("utf-8")})

    pq.write_table(table, path)
    return path


def read_snapshot(path: str | Path) -> ChainSnapshot:
    """Read a Parquet file written by :func:`write_snapshot` back into a `ChainSnapshot`.

    Snapshot-level fields come entirely from the embedded metadata, never from the filename
    or directory structure, so a relocated or renamed file still reads back identically.
    """
    table = pq.read_table(path)

    raw_meta = table.schema.metadata.get(_METADATA_KEY) if table.schema.metadata else None
    if raw_meta is None:
        raise ValueError(f"{path}: missing '{_METADATA_KEY.decode()}' snapshot metadata")
    meta = json.loads(raw_meta.decode("utf-8"))

    # `to_pylist()` on a pyarrow column yields native Python `None` for nulls -- never NaN or
    # 0 -- which is what preserves the "unknown" vs. "genuinely zero/0.0" distinction through
    # the round trip. Going through pandas here would risk silently upcasting an int64+null
    # column to float64 and turning `open_interest=None` into `NaN`, or `None` into `0` for a
    # non-nullable dtype -- exactly the corruption T08's GEX aggregates depend on not happening.
    cols = {name: table.column(name).to_pylist() for name in _COLUMN_NAMES}

    contracts = [
        OptionContract(
            occ_symbol=cols["occ_symbol"][i],
            root=cols["root"][i],
            underlying=Underlying(cols["underlying"][i]),
            expiry=cols["expiry"][i],
            settlement=Settlement(cols["settlement"][i]),
            strike=cols["strike"][i],
            right=Right(cols["right"][i]),
            bid=cols["bid"][i],
            ask=cols["ask"][i],
            last=cols["last"][i],
            volume=cols["volume"][i],
            open_interest=cols["open_interest"][i],
            iv=cols["iv"][i],
            delta=cols["delta"][i],
            gamma=cols["gamma"][i],
            vega=cols["vega"][i],
            theta=cols["theta"][i],
            multiplier=cols["multiplier"][i],
            last_trade_time=cols["last_trade_time"][i],
        )
        for i in range(table.num_rows)
    ]

    return ChainSnapshot(
        underlying=Underlying(meta["underlying"]),
        spot=meta["spot"],
        captured_at=dt.datetime.fromisoformat(meta["captured_at"]),
        source=meta["source"],
        delayed_minutes=meta["delayed_minutes"],
        contracts=contracts,
    )
