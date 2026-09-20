"""Fixed rotation-group membership lists (T50, plans/continuation/04-sector-rotation.md).

The plan's "Data" section names three fixed groups ("Groups are fixed lists in code drawn
from `SCAN_UNIVERSE`") plus two benchmark choices. This module is nothing but those five
literal lists, split out of `app.modules.gex.scan.rotation` into their own tiny module because the plan
says so explicitly ("T49 and T53 may import them") -- two other, not-yet-built continuation-
plan tools want the same group membership without importing the rotation math that comes with
it, and a future `app.modules.gex.scan.rotation` change must never silently change what "sectors" means to
those other tools.

**Literal tuples, not a read of `settings.scan_universe`.** Every symbol below is already a
member of `SCAN_UNIVERSE` (verified against the live `Settings.SCAN_UNIVERSE` default before
this task started -- see the T50 task brief's "State of the world"), but this module does not
import `app.core.config` to re-derive that membership at runtime. Two reasons: (1) purity -- this
package (`app.modules.gex.scan.*`) must stay import-free of anything that pulls in `pydantic_settings`
machinery transitively, the same "no HTTP, DB, filesystem or logging" contract every other
`app.modules.gex.scan` module states for itself; (2) stability -- `SCAN_UNIVERSE` is a single flat string a
future task may reorder or extend for an unrelated reason (a new bars-only symbol, say), and
this module's three groups must not silently gain or lose a member just because that string
changed shape. If a member is ever removed from `SCAN_UNIVERSE` entirely, `app.modules.gex.storage
.bars_repository.read_universe_closes` already degrades that column to all-`NA` rather than
raising (see that function's own docstring) -- this module has no comparable guard to add
because it holds no data, only names.

Membership is copied verbatim from the plan's "Data" section:

* **`SECTORS`** -- the 11 SPDR Select Sector ETFs (all of GICS' 11 sectors, one ETF each).
* **`INDUSTRIES`** -- 11 narrower industry/thematic ETFs (semiconductors, biotech, regional
  banks, oil & gas E&P, homebuilders/housing, retail, software, innovation, airlines) plus
  `GDX` (gold miners) -- the plan's own list, including the one entry (`GDX`) that also
  happens to appear in `Settings.SCAN_UNIVERSE`'s commodity section; it belongs to this group
  by the plan's explicit list, not because of where it sits in that unrelated string.
* **`ASSETS`** -- 12 cross-asset ETFs (equity index, gold, silver, oil, long bonds, high
  yield, dollar, and three international/EM equity regions) for a broad "what's actually
  attracting flow across asset classes" rotation view, as distinct from the two equity-only
  groups above.
* **`BENCHMARKS`** -- the plan's two benchmark choices for the rotation math: `SPY`
  (cap-weighted S&P 500, the default) and `RSP` (equal-weighted S&P 500, the plan's "optional"
  toggle -- also the equal-weight leg of `app.modules.gex.scan.rotation.sector_breadth`'s own RSP/SPY
  breadth reading, which is why `RSP` is in `SCAN_UNIVERSE` at all even though it never
  otherwise appears in any of the three rotation groups).
"""

from __future__ import annotations

__all__ = ["ASSETS", "BENCHMARKS", "DEFAULT_BENCHMARK", "GROUPS", "INDUSTRIES", "SECTORS"]

#: The 11 SPDR Select Sector ETFs. Plan's "Data" section, verbatim.
SECTORS: tuple[str, ...] = (
    "XLK",
    "XLF",
    "XLE",
    "XLV",
    "XLI",
    "XLY",
    "XLP",
    "XLU",
    "XLB",
    "XLRE",
    "XLC",
)

#: Narrower industry/thematic ETFs plus gold miners. Plan's "Data" section, verbatim.
INDUSTRIES: tuple[str, ...] = (
    "SMH",
    "XBI",
    "KRE",
    "XOP",
    "ITB",
    "XHB",
    "XRT",
    "IGV",
    "ARKK",
    "JETS",
    "GDX",
)

#: Cross-asset ETFs (equities, metals, energy, rates, credit, FX, EM/international equities).
#: Plan's "Data" section, verbatim.
ASSETS: tuple[str, ...] = (
    "SPY",
    "QQQ",
    "IWM",
    "GLD",
    "SLV",
    "USO",
    "TLT",
    "HYG",
    "UUP",
    "EEM",
    "EFA",
    "FXI",
)

#: `GET /api/gex/scan/rotation?group=` accepts exactly these three names -- the API layer's 422
#: validation (`app.modules.gex.api.scan._validate_group`) rejects anything else, mirroring how `n`/`k`
#: are validated against a fixed menu in that same module.
GROUPS: dict[str, tuple[str, ...]] = {
    "sectors": SECTORS,
    "industries": INDUSTRIES,
    "assets": ASSETS,
}

#: The rotation page's two benchmark choices (plan: "benchmark (`SPY` default, `RSP`
#: optional)"). `SPY` is cap-weighted, `RSP` equal-weighted -- the same pair
#: `app.modules.gex.scan.rotation.sector_breadth` reads to measure equal- vs cap-weight leadership.
BENCHMARKS: tuple[str, ...] = ("SPY", "RSP")
DEFAULT_BENCHMARK = "SPY"
