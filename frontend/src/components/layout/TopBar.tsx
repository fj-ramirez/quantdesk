/**
 * Top bar: symbol switcher, expiry filter, snapshot selector, theme toggle — the T12
 * deliverable list, verbatim. All three data controls read/write URL state
 * (`useDashboardParams`) so nothing here holds its own copy of "what's selected".
 */
import {
  CORE_UNDERLYINGS,
  EXPIRY_FILTERS,
  EXPIRY_FILTER_LABELS,
  EXTENDED_UNDERLYINGS,
  type Underlying,
} from '../../api/types';
import { useGexResult, useSnapshots } from '../../api/queries';
import { useDashboardParams } from '../../state/urlState';
import { formatFreshness, formatNyDateTime } from '../../lib/time';
import { ThemeToggle } from './ThemeToggle';

function SymbolGroup({
  label,
  symbols,
  symbol,
  onChange,
  className,
}: {
  label: string;
  symbols: readonly Underlying[];
  symbol: Underlying;
  onChange: (s: Underlying) => void;
  className?: string;
}) {
  return (
    <div role="group" aria-label={label} className={className ? `symbol-switcher ${className}` : 'symbol-switcher'}>
      {symbols.map((sym) => (
        <button
          key={sym}
          type="button"
          aria-pressed={sym === symbol}
          disabled={sym === symbol}
          // T36: the current symbol's `disabled` attribute (kept for "no-op click", not
          // removed) used to be the *only* styling it got, so it inherited the browser's
          // dimmed/grey disabled look -- reading as "this symbol is unavailable" rather than
          // "this is the one you're looking at". `symbol-switcher__button--selected` below
          // overrides that with the app's own accent styling instead.
          className={sym === symbol ? 'symbol-switcher__button symbol-switcher__button--selected' : 'symbol-switcher__button'}
          onClick={() => onChange(sym)}
        >
          {sym}
        </button>
      ))}
    </div>
  );
}

/** T47: the switcher gains a second group -- `CORE_UNDERLYINGS` (the 16:20 EOD five, unchanged
 * from T12/T36) and `EXTENDED_UNDERLYINGS` (T47's sector/industry ETFs, captured separately at
 * 16:45 ET). Two `role="group"` regions rather than one flat list of 28 buttons, so a screen
 * reader (and a human) can tell "the P0 five" from "everything else" the same way
 * `/api/health/capture`'s `symbols`/`extended` split does on the backend. */
function SymbolSwitcher({ symbol, onChange }: { symbol: Underlying; onChange: (s: Underlying) => void }) {
  return (
    <div className="symbol-switcher-groups">
      <SymbolGroup label="Symbol" symbols={CORE_UNDERLYINGS} symbol={symbol} onChange={onChange} />
      <SymbolGroup
        label="Symbol (extended)"
        symbols={EXTENDED_UNDERLYINGS}
        symbol={symbol}
        onChange={onChange}
        className="symbol-switcher--extended"
      />
    </div>
  );
}

function ExpiryFilterSelect({
  filter,
  onChange,
}: {
  filter: (typeof EXPIRY_FILTERS)[number];
  onChange: (f: (typeof EXPIRY_FILTERS)[number]) => void;
}) {
  return (
    <label className="topbar-field">
      <span className="topbar-field__label">Expiry</span>
      <select value={filter} onChange={(e) => onChange(e.target.value as (typeof EXPIRY_FILTERS)[number])}>
        {EXPIRY_FILTERS.map((f) => (
          <option key={f} value={f}>
            {EXPIRY_FILTER_LABELS[f]}
          </option>
        ))}
      </select>
    </label>
  );
}

function SnapshotSelector({
  symbol,
  snapshotId,
  onChange,
}: {
  symbol: Underlying;
  snapshotId: string | null;
  onChange: (id: string | null) => void;
}) {
  const { data: snapshots } = useSnapshots(symbol);
  return (
    <label className="topbar-field">
      <span className="topbar-field__label">Snapshot</span>
      <select value={snapshotId ?? 'latest'} onChange={(e) => onChange(e.target.value === 'latest' ? null : e.target.value)}>
        <option value="latest">Latest</option>
        {snapshots?.map((snap) => (
          <option key={snap.id} value={String(snap.id)}>
            {formatNyDateTime(snap.captured_at)}
            {snap.is_eod ? ' (EOD)' : ''}
          </option>
        ))}
      </select>
    </label>
  );
}

/** "As of 11:45 AM ET · Delayed 15m" during the session, or "At Friday's close (4:00 PM ET)"
 * once the market that produced this snapshot has closed (T34) — `formatFreshness` decides
 * which, from `effective_at` vs `captured_at`, so no market-hours logic lives here. Reads
 * from whichever snapshot the URL state currently points at. */
function DataFreshnessBadge({ symbol, filter, snapshotId }: { symbol: Underlying; filter: (typeof EXPIRY_FILTERS)[number]; snapshotId: string | null }) {
  const { data, isLoading, isError } = useGexResult(symbol, filter, snapshotId);
  if (isLoading) return <span className="freshness-badge" aria-live="polite">Loading…</span>;
  if (isError || !data) return <span className="freshness-badge" aria-live="polite">Data unavailable</span>;
  return (
    <span className="freshness-badge" aria-live="polite">
      {formatFreshness(data.snapshot)}
    </span>
  );
}

export function TopBar() {
  const { symbol, filter, snapshotId, setSymbol, setFilter, setSnapshotId } = useDashboardParams();

  return (
    <header className="topbar">
      <SymbolSwitcher symbol={symbol} onChange={setSymbol} />
      <div className="topbar-fields">
        <ExpiryFilterSelect filter={filter} onChange={setFilter} />
        <SnapshotSelector symbol={symbol} snapshotId={snapshotId} onChange={setSnapshotId} />
      </div>
      <div className="topbar-meta">
        <DataFreshnessBadge symbol={symbol} filter={filter} snapshotId={snapshotId} />
        <ThemeToggle />
      </div>
    </header>
  );
}
