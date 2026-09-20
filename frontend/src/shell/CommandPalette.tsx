/**
 * T63 — client-side command/search palette, Ctrl/Cmd+K anywhere in the app
 * (`plans/ui-ux-refresh/README.md`'s "Shared shell" spec). Two kinds of results, no new API
 * call for either:
 *
 *  - Route navigation: `navConfig.ts`'s `flattenNavItems()`, the same ten routes `SideRail`
 *    renders — one source of truth, so a route the rail can reach is always reachable here
 *    too and vice versa.
 *  - Existing-symbol switching: `CORE_UNDERLYINGS`/`EXTENDED_UNDERLYINGS`
 *    (`api/types.ts`), the exact same data `AssetSelector` already reads — no new symbol
 *    list, no new fetch.
 *
 * Selecting a route navigates there, carrying the current search string forward (same
 * "don't drop query params" rule every nav link already follows). Selecting a symbol either
 * updates the `symbol` param in place (if the current route already reads one —
 * `/gex/dashboard`, `/gex/report`, `/gex/history`) or, from a universe page with no symbol
 * slot of its own, jumps to `/gex/dashboard?symbol=<X>` — the one page a bare symbol switch
 * unambiguously means "show me this".
 *
 * The palette is mounted by `AppFrame` (every GEX route) and by `Launcher` (`/`), so Ctrl/
 * Cmd+K works on both; the launcher's search pill opens it through `commandPaletteBus`.
 *
 * Escape/backdrop-click/focus-trap/focus-return is `useOverlayDismiss`, the same hook
 * `SideRail`'s narrow drawer uses.
 */
import { useCallback, useEffect, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { CORE_UNDERLYINGS, EXTENDED_UNDERLYINGS, type Underlying } from '../modules/gex/api/types';
import { useDashboardParams } from '../modules/gex/state/urlState';
import { flattenNavItems } from './navConfig';
import { COMMAND_PALETTE_OPEN_EVENT } from './commandPaletteBus';
import { IconSearch } from './icons';
import { useOverlayDismiss } from './useOverlayDismiss';

/** The three routes that read a `symbol` search param today (T62's baseline). Selecting a
 * symbol while on one of these keeps the current page and only swaps the symbol; from any
 * other route it navigates to `/dashboard` instead. */
const SYMBOL_AWARE_PATHS: ReadonlySet<string> = new Set([
  '/gex/dashboard',
  '/gex/report',
  '/gex/history',
]);

/** Where a bare symbol goes from a page with no symbol slot of its own. T75 moved the whole
 * module one segment deeper; both this and `SYMBOL_AWARE_PATHS` above still named the
 * pre-T75 paths, so every symbol pick navigated to a route that no longer exists. */
const SYMBOL_FALLBACK_PATH = '/gex/dashboard';

interface RouteEntry {
  kind: 'route';
  entryKey: string;
  label: string;
  hint: string;
  to: string;
}

interface SymbolEntry {
  kind: 'symbol';
  entryKey: string;
  label: string;
  hint: string;
  symbol: Underlying;
}

type PaletteEntry = RouteEntry | SymbolEntry;

function buildEntries(): PaletteEntry[] {
  const routes: RouteEntry[] = flattenNavItems().map((item) => ({
    kind: 'route',
    entryKey: `route-${item.key}`,
    label: item.label,
    hint: 'Go to page',
    to: item.to,
  }));
  const symbols: SymbolEntry[] = [...CORE_UNDERLYINGS, ...EXTENDED_UNDERLYINGS].map((sym) => ({
    kind: 'symbol',
    entryKey: `symbol-${sym}`,
    label: sym,
    hint: 'Switch symbol',
    symbol: sym,
  }));
  return [...routes, ...symbols];
}

const ALL_ENTRIES = buildEntries();

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const location = useLocation();
  const navigate = useNavigate();
  const { setSymbol } = useDashboardParams();

  const close = useCallback(() => {
    setOpen(false);
    setQuery('');
    setActiveIndex(0);
  }, []);

  const containerRef = useOverlayDismiss<HTMLDivElement>(open, close);

  // Global Ctrl/Cmd+K toggle -- works from anywhere, including while focus sits inside a
  // page-level control, per the plan's "keyboard-first paths" principle. `preventDefault`
  // stops the browser's own Ctrl/Cmd+K (an address-bar/search shortcut in some browsers)
  // from firing alongside it.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen((v) => !v);
      }
    }
    function onOpenRequest() {
      setOpen(true);
    }
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener(COMMAND_PALETTE_OPEN_EVENT, onOpenRequest);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener(COMMAND_PALETTE_OPEN_EVENT, onOpenRequest);
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return ALL_ENTRIES;
    return ALL_ENTRIES.filter((entry) => entry.label.toLowerCase().includes(q));
  }, [query]);

  // Resets on every keystroke, at the same call site that changes `query` -- not a
  // `useEffect`, so this doesn't trigger a second, cascading render after the query change
  // already committed (react-hooks/set-state-in-effect).
  const onQueryChange = useCallback((next: string) => {
    setQuery(next);
    setActiveIndex(0);
  }, []);

  const select = useCallback(
    (entry: PaletteEntry) => {
      if (entry.kind === 'route') {
        navigate({ pathname: entry.to, search: location.search });
      } else if (SYMBOL_AWARE_PATHS.has(location.pathname)) {
        setSymbol(entry.symbol);
      } else {
        navigate({ pathname: SYMBOL_FALLBACK_PATH, search: `?symbol=${entry.symbol}` });
      }
      close();
    },
    [close, location.pathname, location.search, navigate, setSymbol],
  );

  function onInputKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const entry = filtered[activeIndex];
      if (entry) select(entry);
    }
  }

  if (!open) return null;

  const listboxId = 'command-palette-listbox';
  const active = filtered[activeIndex];

  return (
    <div className="command-palette-backdrop">
      <div className="command-palette" role="dialog" aria-modal="true" aria-label="Command palette" ref={containerRef}>
        <div className="command-palette__input-row">
          <IconSearch className="command-palette__icon" />
          <input
            type="text"
            className="command-palette__input"
            placeholder="Go to a page or switch symbol…"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            onKeyDown={onInputKeyDown}
            role="combobox"
            aria-expanded="true"
            aria-controls={listboxId}
            aria-autocomplete="list"
            aria-activedescendant={active ? active.entryKey : undefined}
          />
        </div>
        <ul className="command-palette__list" role="listbox" id={listboxId} aria-label="Results">
          {filtered.length === 0 && <li className="command-palette__empty">No matches</li>}
          {filtered.map((entry, index) => (
            <li
              key={entry.entryKey}
              id={entry.entryKey}
              role="option"
              aria-selected={index === activeIndex}
              className={`command-palette__item${index === activeIndex ? ' command-palette__item--active' : ''}`}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => select(entry)}
            >
              <span className="command-palette__item-label">{entry.label}</span>
              <span className="command-palette__item-hint">{entry.hint}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
