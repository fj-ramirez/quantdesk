/**
 * Top bar: symbol switcher, expiry filter, snapshot selector, theme toggle — the T12
 * deliverable list, verbatim. All three data controls read/write URL state
 * (`useDashboardParams`) so nothing here holds its own copy of "what's selected".
 */
import { EXPIRY_FILTERS, EXPIRY_FILTER_LABELS, UNDERLYINGS, type Underlying } from '../../api/types';
import { useGexResult, useSnapshots } from '../../api/queries';
import { useDashboardParams } from '../../state/urlState';
import { formatDelay, formatNyDateTime, formatNyTime } from '../../lib/time';
import { ThemeToggle } from './ThemeToggle';

function SymbolSwitcher({ symbol, onChange }: { symbol: Underlying; onChange: (s: Underlying) => void }) {
  return (
    <div role="group" aria-label="Symbol" className="symbol-switcher">
      {UNDERLYINGS.map((sym) => (
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

/** "As of 11:45 AM ET · Delayed 15m" — surfaces the snapshot's own timestamp and delay
 * entitlement (docs/schema.md `delayed_minutes`) so a stale/delayed view is never mistaken
 * for live data. Reads from whichever snapshot the URL state currently points at. */
function DataFreshnessBadge({ symbol, filter, snapshotId }: { symbol: Underlying; filter: (typeof EXPIRY_FILTERS)[number]; snapshotId: string | null }) {
  const { data, isLoading, isError } = useGexResult(symbol, filter, snapshotId);
  if (isLoading) return <span className="freshness-badge" aria-live="polite">Loading…</span>;
  if (isError || !data) return <span className="freshness-badge" aria-live="polite">Data unavailable</span>;
  return (
    <span className="freshness-badge" aria-live="polite">
      As of {formatNyTime(data.snapshot.captured_at)} &middot; {formatDelay(data.snapshot.delayed_minutes)}
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
