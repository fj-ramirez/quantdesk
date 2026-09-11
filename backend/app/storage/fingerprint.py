"""Content fingerprint for a captured chain (TASKS.md T71).

The problem this exists to solve, in one sentence: **the vendor's timestamp is not a reliable
identity for the vendor's data**, so at intraday polling cadence the capture path needs a way
to ask "is this payload the same chain I already stored?" that does not consult a clock.

T34 established that Cboe's top-level ``timestamp`` is payload-*generation* time, not
data-effective time -- verified live 2026-09-04 at 17:55 ET, nearly two hours after the close,
with the field advancing on every request while the quotes underneath stayed frozen at 16:00.
`app.jobs.capture._persist_sync`'s existing duplicate check keys on
``(underlying, captured_at)``, which catches a genuine re-fire of the same request but is blind
to exactly the case T18's 15-minute polling introduces: a slot polled while the feed has not
refreshed, which yields a *fresh* timestamp over *identical* data. Without this module each
such poll would write another Parquet file, another index row and another set of level rows
describing an instant that never happened -- and T20's intraday timeline would plot them as
real readings.

Design notes, all of which matter for correctness rather than style:

* **Timestamps are excluded from the hash entirely.** Not just ``captured_at`` but every
  per-contract ``last_trade_time`` too. Including any of them would defeat the purpose, since
  those are the fields that move when nothing else does.
* **``None`` and ``0`` hash differently**, enforced by :func:`_token`'s type-tagged encoding.
  This is the open-interest rule (invariant 3) applied to hashing: ``None`` means *unknown* and
  excludes the contract from GEX, ``0`` means *genuinely zero* and includes it. A fingerprint
  that collapsed them would let a chain whose OI went from unknown to zero -- a real, material
  change -- look unchanged.
* **Contract order does not affect the hash.** The lines are sorted, so a provider that
  reorders its payload between calls (or a future provider that emits a different natural
  order) does not read as a content change.
* **Floats are encoded via ``repr``**, which round-trips exactly in Python. Formatting to a
  fixed number of decimal places would make two genuinely different quotes collide.

Only the fields that can change the computed GEX are hashed: identity, strike, right, expiry,
bid, ask, IV, open interest, and spot. Vendor greeks are deliberately omitted -- the engine
computes its own from IV (PLAN.md §2), so a vendor greek refresh that moves nothing the engine
reads is not a content change worth storing a new snapshot for.
"""

from __future__ import annotations

import hashlib

from app.models.chain import ChainSnapshot

__all__ = ["chain_fingerprint"]

#: Bumped if the hashed field set ever changes, so old and new fingerprints can never compare
#: equal across a schema change. Stored as the first thing in the digest input.
_FINGERPRINT_VERSION = "1"


def _token(value: float | int | None) -> str:  # noqa: PYI041 - the int/float split is runtime behaviour
    """Encode one nullable number so that `None`, `0` and `0.0` are all distinguishable.

    The type tag is the whole point: without it `0` (int) and `0.0` (float) would produce the
    same text, and `None` would need a magic numeric stand-in that some real value could
    eventually collide with.

    The annotation keeps `int` spelled out even though `float` would subsume it for a type
    checker (PYI041), because here the distinction is not cosmetic: `open_interest` is an `int`
    and `bid`/`ask`/`iv` are `float`s, and this function deliberately tags them differently.
    Collapsing the annotation would hide the one property the callers depend on.
    """
    if value is None:
        return "~"
    if isinstance(value, bool):  # bool is an int subclass; tag it separately rather than as 0/1
        return f"b{int(value)}"
    if isinstance(value, int):
        return f"i{value}"
    return f"f{float(value)!r}"


def chain_fingerprint(snapshot: ChainSnapshot) -> str:
    """Stable SHA-256 hex digest of everything in `snapshot` that can move GEX.

    Two snapshots of the same underlying with equal fingerprints describe the same chain, no
    matter what their `captured_at` values say. Two with different fingerprints differ in at
    least one quote, IV, open interest, strike, contract or in spot.

    Returns:
        64 hex characters, matching `app.models.db.Snapshot.content_hash`'s column width.
    """
    digest = hashlib.sha256()
    digest.update(_FINGERPRINT_VERSION.encode())
    digest.update(b"\x00")
    digest.update(snapshot.underlying.value.encode())
    digest.update(b"\x00")
    digest.update(_token(snapshot.spot).encode())

    lines = sorted(
        "|".join(
            (
                contract.occ_symbol,
                contract.right.value,
                contract.expiry.isoformat(),
                _token(contract.strike),
                _token(contract.bid),
                _token(contract.ask),
                _token(contract.iv),
                _token(contract.open_interest),
            )
        )
        for contract in snapshot.contracts
    )
    for line in lines:
        digest.update(b"\x01")
        digest.update(line.encode())
    return digest.hexdigest()
