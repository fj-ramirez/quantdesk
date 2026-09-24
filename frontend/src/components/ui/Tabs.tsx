/**
 * T122 — in-section tabs. A page that stacked every block one under another (Overview,
 * Opportunities, Scan) now shows one block at a time behind a tab strip, so the reader picks
 * the question instead of scrolling past the others to reach it.
 *
 * WAI-ARIA tabs pattern: `role="tablist"`/`tab`/`tabpanel`, roving `tabIndex`, Left/Right/Home/
 * End move between tabs and activate them (automatic activation — every panel here is cheap
 * enough to render on focus). Only the active panel is mounted, so a panel that owns its own
 * query (`TrackRecord`) fetches only when the reader opens it.
 *
 * Controlled, like `SegmentedControl`: the active tab is **URL state** (`useTabParam`), because
 * "the no-trade list on Opportunities" is a view a user would link to or reload into — the
 * frontend's state rule (`context/frontend.md`, "State").
 */
import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react';

export interface TabDef<T extends string> {
  value: T;
  label: string;
  /** A count shown beside the label ("Ranked 25"). `null` renders nothing — not "0". */
  count?: number | null;
}

export interface TabsProps<T extends string> {
  /** Accessible name of the tab strip. */
  label: string;
  tabs: readonly TabDef<T>[];
  active: T;
  onChange: (value: T) => void;
  /** Renders the active panel. Only the active tab's content is mounted. */
  children: ReactNode;
  className?: string;
  /** Compact controls for the tab row itself, right-aligned (e.g. the report's full-text
   * button). They belong to the page, not to any one tab, so they stay put when tabs change. */
  actions?: ReactNode;
  /** Pin the tab row under the frame's sticky context bar while the page scrolls (desktop
   * widths only -- see `.ui-tabs--sticky`). */
  sticky?: boolean;
}

export function Tabs<T extends string>({
  label,
  tabs,
  active,
  onChange,
  children,
  className,
  actions,
  sticky,
}: TabsProps<T>) {
  const baseId = useId();
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const activeIndex = Math.max(
    0,
    tabs.findIndex((t) => t.value === active),
  );

  function move(to: number) {
    const next = (to + tabs.length) % tabs.length;
    onChange(tabs[next].value);
    tabRefs.current[next]?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === 'ArrowRight') move(activeIndex + 1);
    else if (event.key === 'ArrowLeft') move(activeIndex - 1);
    else if (event.key === 'Home') move(0);
    else if (event.key === 'End') move(tabs.length - 1);
    else return;
    event.preventDefault();
  }

  const tabId = (i: number) => `${baseId}-tab-${i}`;
  const panelId = `${baseId}-panel`;

  return (
    <div className={['ui-tabs', sticky ? 'ui-tabs--sticky' : '', className ?? ''].filter(Boolean).join(' ')}>
      <div className="ui-tabs__bar">
      <div role="tablist" aria-label={label} className="ui-tabs__list">
        {tabs.map((tab, i) => {
          const selected = i === activeIndex;
          return (
            <button
              key={tab.value}
              ref={(el) => {
                tabRefs.current[i] = el;
              }}
              id={tabId(i)}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={panelId}
              tabIndex={selected ? 0 : -1}
              className={selected ? 'ui-tabs__tab ui-tabs__tab--active' : 'ui-tabs__tab'}
              onClick={() => onChange(tab.value)}
              onKeyDown={onKeyDown}
            >
              {tab.label}
              {tab.count != null && <span className="ui-tabs__count">{tab.count}</span>}
            </button>
          );
        })}
      </div>
      {actions != null && <div className="ui-tabs__actions">{actions}</div>}
      </div>
      <div role="tabpanel" id={panelId} aria-labelledby={tabId(activeIndex)} className="ui-tabs__panel">
        {children}
      </div>
    </div>
  );
}
