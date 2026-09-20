/**
 * T63 — renamed from `TopBar.tsx` (the plan's `ContextBar` primitive; see
 * `plans/ui-ux-refresh/README.md`'s design-system contract table). Behavior is otherwise
 * unchanged from T55's TopBar: asset dropdown, expiry filter, snapshot selector, theme
 * toggle on a symbol page; a compact bars-freshness toolbar on a scan-family route. Only the
 * outer wrapper's class (`topbar` -> `context-bar`, now `position: sticky` in index.css) and
 * the exported name changed — every control below (`AssetSelector`, the expiry/snapshot
 * selects, the freshness badge, `ThemeToggle`) is untouched, so every existing URL param
 * still flows through exactly as it did as `TopBar`.
 *
 * T55: route-aware. The asset dropdown, expiry filter, snapshot selector and freshness
 * badge only make sense for a *symbol* page (`/`, `/report`, `/history`) — the scan family
 * (`/scan`, `/regime`, `/rotation`, `/flows`, `/overview`) are universe pages with no single
 * symbol/expiry/snapshot selection to show, so on those routes this renders a different,
 * much smaller toolbar (bars freshness + theme toggle) instead. Nothing else about the
 * component changes: the dashboard controls below are otherwise untouched from before T55.
 *
 * The symbol control used to be a flat wall of up to 28 always-visible buttons
 * (`CORE_UNDERLYINGS` + `EXTENDED_UNDERLYINGS`, two `role="group"` rows) -- replaced by
 * `AssetSelector`, a single dropdown trigger that opens a grouped listbox on click. Same
 * underlying data and `setSymbol` contract; only the presentation changed.
 */
import { useLocation } from 'react-router-dom';
import { EXPIRY_FILTERS, EXPIRY_FILTER_LABELS, type Underlying } from '../../api/types';
import { useGexResult, useSnapshots } from '../../api/queries';
import { useLiveLevels } from '../../api/useLiveLevels';
import { useDashboardParams } from '../../state/urlState';
import { formatFreshness, formatNyDateTime } from '../../../../lib/time';
import { ThemeToggle } from '../../../../shell/ThemeToggle';
import { ModuleBadge } from '../../../../shell/ModuleIdentity';
import { SegmentedControl } from '../../../../components/ui/Toolbar';
import { CORE_UNDERLYINGS } from '../../api/types';
import { AssetSelector } from './AssetSelector';
import { BarsFreshness } from '../scan/BarsFreshness';

/** The scan family, per 07-ui.md's "Information architecture" -- kept as one list here so a
 * future scan-family route only needs adding in one place (this set, and `navConfig.ts`'s
 * `NAV_GROUPS`) rather than being independently taught to both. */
const SCAN_FAMILY_PATHS: ReadonlySet<string> = new Set([
  // `/gex` is in this set because Overview became the module's landing page on 2026-09-10 --
  // it is a universe page, so the module root renders the scan toolbar rather than the symbol
  // controls. The dashboard, the actual symbol page, is `/gex/dashboard`. (T75 put the `/gex`
  // segment in front of every one of these; the set is otherwise unchanged.)
  '/gex',
  '/gex/scan',
  '/gex/regime',
  '/gex/rotation',
  '/gex/flows',
  '/gex/overview',
  '/gex/decisions',
]);

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

/** T19 — a dot beside the freshness stamp saying whether this tab is receiving live updates.
 *
 * Deliberately small and deliberately honest. The value of the SSE channel is that the user can
 * *trust* the freshness stamp without reloading, and that trust is only warranted while the
 * connection is up. A silently-dead stream behind a confident-looking timestamp is worse than
 * no indicator at all, which is why 'reconnecting' is shown rather than hidden.
 *
 * Nothing is rendered when the browser has no `EventSource` (jsdom, a prerender): the page then
 * behaves exactly as it did before this task, fetching normally and simply not refreshing on
 * its own, and an indicator claiming otherwise would be a lie.
 */
function LiveIndicator({ symbol }: { symbol: Underlying }) {
  const { status } = useLiveLevels(symbol);
  if (status === 'unsupported') return null;
  const label = status === 'live' ? 'Live' : status === 'connecting' ? 'Connecting…' : 'Reconnecting…';
  return (
    <span className={`live-indicator live-indicator--${status}`} aria-live="polite" title={`Live updates: ${label}`}>
      <span className="live-indicator__dot" aria-hidden="true" />
      {label}
    </span>
  );
}

/**
 * One-click switching for the five core symbols, beside the full 28-symbol dropdown rather
 * than instead of it. `AssetSelector` stays exactly as it was — it is the complete list, and
 * the only way to reach an extended symbol — but reaching SPY from SPX no longer costs two
 * clicks on the page where symbol is the primary axis. Built from `SegmentedControl`, the
 * same primitive every scan-family filter row already uses, so this introduces no new
 * interaction vocabulary; its "Symbol" caption is visually hidden here (kept for assistive
 * tech) because the dropdown beside it already carries one.
 */
function SymbolTabs({ symbol, onChange }: { symbol: Underlying; onChange: (next: Underlying) => void }) {
  return (
    <div className="context-bar__symbols">
      <SegmentedControl
        label="Symbol"
        values={CORE_UNDERLYINGS}
        active={CORE_UNDERLYINGS.includes(symbol as (typeof CORE_UNDERLYINGS)[number]) ? symbol : CORE_UNDERLYINGS[0]}
        render={(value) => value}
        onChange={onChange}
      />
    </div>
  );
}

export function ContextBar() {
  const location = useLocation();
  const { symbol, filter, snapshotId, setSymbol, setFilter, setSnapshotId } = useDashboardParams();

  if (SCAN_FAMILY_PATHS.has(location.pathname)) {
    return (
      <header className="context-bar context-bar--scan">
        <ModuleBadge moduleKey="gex" />
        <BarsFreshness />
        <div className="topbar-meta">
          <ThemeToggle />
        </div>
      </header>
    );
  }

  return (
    <header className="context-bar">
      <ModuleBadge moduleKey="gex" />
      <SymbolTabs symbol={symbol} onChange={setSymbol} />
      <AssetSelector symbol={symbol} onChange={setSymbol} />
      <div className="topbar-fields">
        <ExpiryFilterSelect filter={filter} onChange={setFilter} />
        <SnapshotSelector symbol={symbol} snapshotId={snapshotId} onChange={setSnapshotId} />
      </div>
      <div className="topbar-meta">
        <DataFreshnessBadge symbol={symbol} filter={filter} snapshotId={snapshotId} />
        <LiveIndicator symbol={symbol} />
        <ThemeToggle />
      </div>
    </header>
  );
}
