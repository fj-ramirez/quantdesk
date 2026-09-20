/**
 * `/regime`'s board (T49, `plans/continuation/07-ui.md`'s `/regime` section). A column set
 * over T55's shared `ScanTable`, same as every other scan-family table -- no markup, sorting
 * or null handling reimplemented here.
 *
 * Two things worth reading before touching a column:
 *
 *  - **Wall below/above and Room beyond are magnitudes and levels, not signed distances.**
 *    `RegimeWall.distance_pct`/`distance_atr` are always >= 0 (a wall below spot is a positive
 *    distance below it, by construction) -- they render plainly, with no "formatDistancePct"
 *    sign logic applied. Room beyond, by contrast, *is* a fresh level-vs-spot distance (the
 *    next strike past the nearer wall in the direction of the 5-day move), so it does go
 *    through `formatDistancePct`, plus an arrow naming the direction it was measured in.
 *  - **Flip dist renders the API's own `flip_distance_pct` verbatim, not a recomputation.**
 *    That field's sign convention is `(spot - flip_point) / spot`, the opposite of this file's
 *    `formatDistancePct(level, spot)` helper (`(level - spot) / spot`). Feeding `flip_point`
 *    and `spot` through that helper here would silently flip every row's sign against what the
 *    API, and `reasons`' own prose, say. See `RegimeSymbolRow.flip_distance_pct`'s docstring in
 *    `api/types.ts`.
 *
 * **Distinguishing `stale` from `noise-dominated`.** Both are a null verdict, but for
 * unrelated reasons (`regimeRows.ts`'s module docstring) and must not look alike: the Verdict
 * chip itself already differs (`VerdictCell` -> `StatusChip` -> `theme/vizPalette.ts`'s
 * `statusChipColor`, blue for `stale`, neutral grey for `noise-dominated`), and every cell in
 * a `stale` or `noise-dominated` row additionally carries `regimeRows.ts`'s `rowTintClassFor`
 * class -- a muted (reduced-opacity) tint for `noise-dominated` per 07-ui.md's explicit
 * instruction ("noise-dominated rows get the neutral chip and a muted row style"), and a
 * distinct accent-bordered tint for `stale` that does *not* mute the row, since a stale row's
 * other numbers (walls, trend, IV/RV) are not wrong, only its verdict is unsafe to read --
 * dimming the whole row the same way `noise-dominated` is dimmed would bury that distinction
 * rather than surface it. `ScanTable` owns `<tr>`/`<td>` markup and is not modified here (T55's
 * kit is a stable interface); the tint is applied per-cell, as a `<span>` wrapper matching the
 * cell's own padding, via the `tinted` helper below.
 */
import type { ReactNode } from 'react';
import type { ColumnDef } from '../scan/ScanTable';
import { ScanTable } from '../scan/ScanTable';
import type { SortDir } from '../../state/urlState';
import { PercentileBar } from '../scan/PercentileBar';
import { SymbolCell } from '../scan/SymbolCell';
import { formatDistancePct, formatGex, formatPct, formatRatio, formatStrike } from '../../../../lib/format';
import type { RegimeWall } from '../../api/types';
import { VerdictCell } from './VerdictCell';
import { rowTintClassFor, type RegimeRow, type RoomBeyond } from './regimeRows';

const DASH = '—';

/** The API's own signed percent (see this file's docstring) -- rendered verbatim, only styled
 * (sign, two decimals, trailing `%`), never recomputed from a level and a spot. */
function formatSignedPercentFigure(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

/** `strike · dist% · x.x ATR` (07-ui.md). Both `dist%` and the ATR figure are magnitudes --
 * see this file's docstring for why no sign is applied. */
function formatWall(wall: RegimeWall | null): ReactNode {
  if (!wall) return DASH;
  return (
    <span className="regime-wall">
      {formatStrike(wall.strike)} · {wall.distance_pct.toFixed(2)}% · {wall.distance_atr.toFixed(1)} ATR
    </span>
  );
}

function formatRoomBeyond(room: RoomBeyond | null, spot: number | null): ReactNode {
  // `spot` is null only for a symbol with no snapshot captured yet, where `room` is null too --
  // but the distance is a percentage *of spot*, so it is unrenderable without one either way.
  if (!room || spot == null) return DASH;
  const arrow = room.direction === 'up' ? '↑' : '↓';
  return (
    <span className="regime-room-beyond">
      {arrow} {formatDistancePct(room.strike, spot)}
    </span>
  );
}

/** Always `null` today (`api/types.ts`'s `RegimeSymbolRow.zero_dte_share` docstring) --
 * rendered as a dash with a tooltip naming *why*, never a bare dash and never `0%`. */
function formatZeroDteShare(value: number | null): ReactNode {
  if (value == null) {
    return (
      <span
        className="regime-zero-dte"
        title="Not observable from an end-of-day chain: the same-day expiry is already gone from the payload by capture time (plans/continuation/03-regime-board.md, 'The 0DTE share is not derivable from an EOD snapshot')."
      >
        {DASH}
      </span>
    );
  }
  return <span>{formatPct(value)}</span>;
}

/** Wraps a cell's content in the row's tint class (or returns it untouched for a genuine
 * verdict row) -- see this file's docstring. `display: inline-block` plus the class's own
 * negative-margin/padding pair (`index.css`) makes the span fill the `<td>`'s own padding box,
 * so the tint reads as covering the whole cell rather than just the text inside it. */
function tinted(row: RegimeRow, node: ReactNode): ReactNode {
  const cls = rowTintClassFor(row.verdictGroup);
  if (!cls) return node;
  return <span className={cls}>{node}</span>;
}

export interface RegimeTableProps {
  rows: RegimeRow[];
  /** The page's current expiry filter, as a raw query string fragment (`filter=ALL`) --
   * carried onto the symbol link via `SymbolCell`'s `search` prop so a click preserves it. */
  filterSearch: string;
  sort: string | null;
  dir: SortDir;
  onSort: (key: string) => void;
}

export function RegimeTable({ rows, filterSearch, sort, dir, onSort }: RegimeTableProps) {
  const columns: ColumnDef<RegimeRow>[] = [
    {
      key: 'symbol',
      header: 'Symbol',
      sortable: true,
      // Every row on this board has an option chain (07-ui.md: "always a link here") --
      // `SymbolCell` still applies the link-or-text rule itself, so this stays correct even if
      // that guarantee is ever relaxed.
      format: (_value, row) => tinted(row, <SymbolCell symbol={row.symbol} search={filterSearch} />),
    },
    {
      key: 'defaultSortKey',
      header: 'Verdict',
      sortable: true,
      format: (_value, row) => tinted(row, <VerdictCell row={row} />),
    },
    {
      key: 'netGex',
      header: 'Net GEX',
      align: 'right',
      sortable: true,
      format: (value, row) => tinted(row, formatGex(value as number | null)),
    },
    {
      key: 'ratio',
      header: 'net/abs',
      align: 'right',
      sortable: true,
      title: 'Net gamma as a share of gross gamma',
      format: (value, row) => tinted(row, formatPct(value as number | null)),
    },
    {
      key: 'flipDistancePct',
      header: 'Flip dist',
      align: 'right',
      sortable: true,
      format: (value, row) => tinted(row, formatSignedPercentFigure(value as number | null)),
    },
    {
      key: 'wallBelow',
      header: 'Wall below',
      align: 'right',
      format: (value, row) => tinted(row, formatWall(value as RegimeWall | null)),
    },
    {
      key: 'wallAbove',
      header: 'Wall above',
      align: 'right',
      format: (value, row) => tinted(row, formatWall(value as RegimeWall | null)),
    },
    {
      key: 'roomBeyond',
      header: 'Room beyond',
      align: 'right',
      title: "Distance to the next strike past the nearer wall, in the direction of the 5-day move",
      format: (value, row) => tinted(row, formatRoomBeyond(value as RoomBeyond | null, row.spot)),
    },
    {
      key: 'zeroDteShare',
      header: '0DTE share',
      align: 'right',
      format: (value, row) => tinted(row, formatZeroDteShare(value as number | null)),
    },
    {
      key: 'ivRvRatio',
      header: 'IV/RV',
      align: 'right',
      sortable: true,
      format: (value, row) => tinted(row, formatRatio(value as number | null)),
    },
    {
      key: 'trendPct',
      header: 'Trend pct',
      align: 'right',
      sortable: true,
      title: "T45's cross-sectional trend composite",
      format: (value, row) => tinted(row, <PercentileBar value={value as number | null} />),
    },
  ];

  return (
    <ScanTable
      columns={columns}
      rows={rows}
      sort={sort}
      dir={dir}
      onSort={onSort}
      rowKey={(row) => row.symbol}
      caption="Dealer positioning regime by symbol"
    />
  );
}
