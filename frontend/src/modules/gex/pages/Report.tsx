/**
 * T40 — the options-intelligence report view, rendering T39's `GET /api/gex/report/{underlying}`.
 *
 * Layout follows the screenshot the user supplied: a `{SYMBOL} Analysis Results` header, three
 * summary cards (price, volatility, sentiment), resistance and support level chips, then a
 * collapsible full-text panel. **Its numbers do not**: every figure here comes from our own
 * engine via T39, because the supplied example was reconciled against the very GLD chain it
 * claimed to describe and had the put/call ratio inverted, max pain wrong, IV wrong and its
 * support listed above its resistance. See `backend/app/modules/gex/gex/report.py`'s module docstring.
 *
 * Three things this page must keep doing, each of which is a rule from an earlier task:
 *
 *  - **Never render a raw response body** (T37). A symbol with no snapshot is an empty state
 *    with a capture affordance, not an error, and `ApiError.detail` is the parsed envelope —
 *    `{"detail": ...}` never reaches the screen.
 *  - **Report staleness honestly** (T34) via `formatFreshness`, which is why the badge sits at
 *    the top of the page rather than in a footer: a playbook drawn from a stale chain is worse
 *    than no playbook, so the reader should meet the data's age before its conclusions.
 *  - **Say when we cannot say.** The IV regime label is null on a single-snapshot database and
 *    renders as "insufficient history"; a noise-dominated chain (DIA) shows that label instead
 *    of a direction. Neither is a loading state and neither should be styled as a failure.
 *
 * **T67.** This page carried 82 inline-`style` clusters (`01-ux-baseline.md`'s baseline audit)
 * — the heaviest inline-style debt in the app — because every value here reads `vizPaletteFor`
 * for its colour, including plain card chrome that was never actually a chart/level colour.
 * Rebuilt onto T64's shared primitives (`Surface`, `MetricCard`, `DataTableFrame`, `EmptyState`,
 * `PageHeader`) with one deliberate exception, the same one `KeyLevels.tsx` already established
 * for its net-GEX sign dot: `LevelChip`'s support/resistance colour is a genuine chart/level
 * colour (T40's red/green pair, documented in `theme/vizPalette.ts`) and stays driven by
 * `vizPaletteFor`, set inline on that one element's border and dot only, never on text. Every
 * other colour in this file now reads an `index.css` token instead.
 *
 * The page-body symbol/expiry `<select>`s this file used to render (duplicating the shell's
 * `AssetSelector`/`ExpiryFilterSelect`) are gone — `ContextBar` is not in `SCAN_FAMILY_PATHS`
 * for `/report` (same as `/dashboard`), so it already renders the full symbol/expiry/snapshot
 * controls above this page, reading and writing the same `symbol`/`filter` URL params via the
 * same `useDashboardParams` hook this component calls. The CFD-spot input has no shell
 * equivalent (it is unique to this page) and stays here, now in `PageHeader`'s `actions` slot.
 *
 * **T69 — density pass.** The five sections that used to always render expanded --  Gamma
 * exposure, Premium selling screen, Playbook, Risk alerts, and Executive summary ("Summary")
 * -- are each now a native `<details>` disclosure, collapsed by default, matching Rotation's
 * "About this chart" precedent and consistent with `FullReportPanel`'s own `aria-expanded`
 * collapse below (left untouched -- both idioms now sit on the page, `<details>` for the new
 * sections, the existing button+`aria-expanded` panel for the full text). "Levels straddling
 * spot" and the three top summary cards (Current price/Volatility/Market sentiment) are
 * deliberately left outside any disclosure -- the plan's named "concise, load-bearing read".
 * Every section's own `<h2>` moved inside its `<summary>` rather than being dropped, so heading
 * -order navigation for assistive tech is unaffected; only its visible position changed (from
 * inside the region to the clickable row above it). No value, disclaimer string, CFD logic, or
 * the capture-now flow changed -- this only moves existing JSX into disclosure wrappers.
 *
 * **T122 -- tabs and an overlay full report.** Five collapsed disclosures read as a row of
 * closed doors, and opening them all ran the page past two and a half screens. They are now
 * in-section tabs (`?tab=`, URL state) -- Summary first, as the concise read, then Gamma
 * exposure, Premium screen, Playbook, Risk alerts. The Risk alerts tab carries its count, so an
 * alert is announced on the tab strip even while its panel is closed; a collapsed `<details>`
 * never said whether it held anything. A tab whose section would be empty (no summary lines,
 * no alerts) is not offered, the same "render nothing" rule as before. The full text report
 * opens in a wide `DetailDrawer` instead of expanding at the foot of the page, and still
 * fetches only once opened. No value, disclaimer, CFD logic or the capture-now flow changed.
 *
 * **T122 -- dense layout (the user's own spec and mockup, 2026-09-23).** The three full-height
 * cards and three full-width level bands pushed the tabs a screen down. Now: price and
 * volatility are compact blocks in the page header's right slot (`HeadlineMetrics`); the tabs
 * sit directly under the header, pinned under the context bar on desktop; the Summary tab holds
 * one Market structure panel (resistance / support / straddling as label-and-chips rows,
 * `LevelRow`) beside Market sentiment, then the summary lines with the full-report button. The
 * CFD-spot input moved to the tab row. Every region keeps its accessible name.
 */
import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { ApiError } from '../api/client';
import { useCaptureSnapshot, useReport, useReportText } from '../api/queries';
import {
  CFD_INSTRUMENTS,
  type CfdLevel,
  type CfdPlaybookEntry,
  type ExpiryFilter,
  type PlaybookEntry,
  type PremiumCandidate,
  type Report as ReportData,
  type ReportLevel,
  type Underlying,
} from '../api/types';
import { EmptyState } from '../components/EmptyState';
import {
  formatCount,
  formatDistancePct,
  formatGex,
  formatIv,
  formatPrice,
  formatStrike,
} from '../../../lib/format';
import { formatFreshness } from '../../../lib/time';
import { useDashboardParams } from '../state/urlState';
import { useTheme } from '../../../theme/ThemeContext';
import { vizPaletteFor } from '../../../theme/vizPalette';
import { DataTableFrame } from '../../../components/ui/DataTableFrame';
import { PageHeader } from '../../../components/ui/PageHeader';
import { Surface } from '../../../components/ui/Surface';
import { DetailDrawer } from '../../../components/ui/DetailDrawer';
import { Tabs, type TabDef } from '../../../components/ui/Tabs';
import { useTabParam } from '../../../components/ui/useTabParam';

const DASH = '—';

/** A distance already expressed in percent by the backend (not a fraction), e.g. `2.02` ->
 * `"+2.02%"`. Distinct from `formatDistancePct`, which takes a level and a spot and does the
 * division itself — the report has already done it, and re-deriving it here from a rounded
 * strike would produce a number that disagrees with the one in the text panel. */
function formatPctValue(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

/** Ratio (0.427) -> "42.7%". Used for the |net| / gross confidence figure. */
function formatRatio(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return `${(value * 100).toFixed(1)}%`;
}

// ---------------------------------------------------------------------------------------
// Small presentational pieces
// ---------------------------------------------------------------------------------------

/**
 * One support or resistance chip.
 *
 * The colour is carried on the border and the dot only, never the text — see
 * `theme/vizPalette.ts` for why (the light-mode red is 3.85:1, below the 4.5:1 text
 * threshold) and for why red/green is safe here despite being the canonical CVD failure: the
 * signed distance and the section heading both restate the side in text, so hue is never the
 * only channel. This is the one place on this page that still reads `vizPaletteFor` directly
 * — every other colour on this page is a plain `index.css` token.
 */
/** `strike -> CfdLevel`, built once per render from a `CfdTranslation` side. `native_strike`
 * is the join key back to the `ReportLevel` it was translated from -- see
 * `backend/app/modules/gex/gex/report.py`'s `CfdLevel` docstring. */
function cfdLevelIndex(levels: CfdLevel[] | undefined): Map<number, CfdLevel> {
  const map = new Map<number, CfdLevel>();
  for (const level of levels ?? []) {
    if (level.native_strike != null) map.set(level.native_strike, level);
  }
  return map;
}

function LevelChip({
  level,
  cfd,
  instrument,
  theme,
}: {
  level: ReportLevel;
  cfd?: CfdLevel;
  instrument?: string;
  theme: 'light' | 'dark';
}) {
  const palette = vizPaletteFor(theme);
  const color = level.side === 'SUPPORT' ? palette.levelSupport : level.side === 'RESISTANCE' ? palette.levelResistance : palette.levelStraddling;
  return (
    <li className="report-level-chip" style={{ borderColor: color }}>
      <span aria-hidden="true" className="report-level-chip__dot" style={{ background: color }} />
      <strong className="report-level-chip__strike">{formatStrike(level.strike)}</strong>
      <span className="report-level-chip__distance">{formatPctValue(level.distance_pct)}</span>
      <span className="report-level-chip__gex">{formatGex(level.net_gex)}</span>
      {/* T41: the same level in the CFD's terms, alongside the native strike -- never in its
          place. The percentage distance above is deliberately not repeated here: it is
          identical in both units by construction, so showing it twice would only invite the
          reader to wonder whether it should differ. */}
      {cfd && instrument && cfd.strike != null && (
        <span className="report-level-chip__cfd">
          [{instrument} {formatStrike(cfd.strike)}]
        </span>
      )}
    </li>
  );
}

/**
 * T122: one row of the Market structure panel -- a label column and its chips flowing
 * horizontally beside it, where each side used to be its own full-width band. The row keeps
 * the region name it had as a band ("Top resistance levels", ...), so a screen reader's
 * landmark list is unchanged. `note` carries the straddling explanation.
 */
function LevelRow({
  heading,
  regionLabel,
  levels,
  cfdIndex,
  instrument,
  emptyMessage,
  note,
  tone,
  theme,
}: {
  heading: string;
  /** Colours the row label with the same semantic hue as its chips. */
  tone: 'resistance' | 'support' | 'straddling';
  regionLabel: string;
  levels: ReportLevel[];
  cfdIndex: Map<number, CfdLevel>;
  instrument?: string;
  emptyMessage: string;
  note?: string | null;
  theme: 'light' | 'dark';
}) {
  return (
    <section aria-label={regionLabel} className="report-level-row">
      <h3 className={`report-level-row__label report-level-row__label--${tone}`}>{heading}</h3>
      <div className="report-level-row__body">
        {levels.length === 0 ? (
          <p className="report-muted">{emptyMessage}</p>
        ) : (
          <ul className="report-level-chips">
            {levels.map((level) => (
              <LevelChip
                key={`${level.side}-${level.strike}`}
                level={level}
                cfd={level.strike == null ? undefined : cfdIndex.get(level.strike)}
                instrument={instrument}
                theme={theme}
              />
            ))}
          </ul>
        )}
        {note && <p className="report-level-row__note">{note}</p>}
      </div>
    </section>
  );
}

/**
 * T122: price and volatility as compact headline blocks in the page header's right slot,
 * replacing two of the three full-height cards. Every line the cards carried is still here,
 * including the IV-regime "insufficient history" answer -- a null label is the correct
 * answer on a short history, never a band to substitute.
 */
function HeadlineMetrics({ report }: { report: ReportData }) {
  const { iv_regime: iv, max_pain: maxPain, cfd } = report;
  return (
    <div className="report-headline">
      <section aria-label="Current price" className="report-headline__metric">
        <span className="report-headline__value">{formatPrice(report.spot)}</span>
        <span className="report-headline__label">Price</span>
        {cfd && (
          <span className="report-headline__line">
            {cfd.instrument} {formatPrice(cfd.cfd_spot)}
          </span>
        )}
        <span className="report-headline__line">
          Max pain {formatStrike(maxPain.strike)} · {formatPctValue(maxPain.distance_pct)}
          {cfd?.max_pain?.strike != null && (
            <span className="report-muted-inline"> [{cfd.instrument} {formatStrike(cfd.max_pain.strike)}]</span>
          )}
        </span>
      </section>
      <section aria-label="Volatility" className="report-headline__metric">
        <span className="report-headline__value">{formatIv(iv.atm_iv)}</span>
        <span className="report-headline__label">Volatility</span>
        <span className="report-headline__line">
          ATM ~{iv.target_dte}d
          {iv.interpolated && iv.lower_dte != null && iv.upper_dte != null
            ? ` · interpolated ${iv.lower_dte}–${iv.upper_dte} DTE`
            : ''}
        </span>
        {/* One line, truncated, with the full sentence on hover -- kept in the DOM (and so
            for screen readers) rather than dropped, because "no label yet" is the answer. */}
        {(() => {
          const text =
            iv.label ??
            `Insufficient history — a regime label needs ${iv.min_history_required} prior snapshots, there are ${iv.history_observations}.`;
          return (
            <span className={iv.label ? 'report-iv-label' : 'report-headline__line report-headline__line--clip report-iv-label--muted'} title={text}>
              {text}
            </span>
          );
        })()}
      </section>
    </div>
  );
}

/** The banner every trade-suggestion section carries. Wording is deliberate and is the same
 * sentence the backend's plain-text renderer prints: this is a screen over the current chain,
 * not advice, and the app never routes an order (PLAN.md's amended scope line). Also reused for
 * the CFD translation note (T41) -- same "quiet callout" visual role, different content. */
function ScreeningNotice({ children }: { children: React.ReactNode }) {
  return <p className="report-note">{children}</p>;
}

function CandidateTable({ rows, caption }: { rows: PremiumCandidate[]; caption: string }) {
  return (
    <DataTableFrame title={caption}>
      {rows.length === 0 ? (
        <p className="report-muted">Nothing quoted beyond this wall in the screened window.</p>
      ) : (
        // T68: unwrapped, this 6-column table forced page-level horizontal overflow at
        // 390px. `.scan-table-container` (overflow-x: auto) is the same contained-scroll
        // wrapper every scan-family table already uses -- no column/value changed.
        <div className="scan-table-container" tabIndex={0}>
        <table className="report-candidate-table">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr>
              <th scope="col">Strike</th>
              <th scope="col">Mid</th>
              <th scope="col">IV</th>
              <th scope="col">DTE</th>
              <th scope="col">From spot</th>
              <th scope="col">OI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.occ_symbol}>
                <td>{formatStrike(row.strike)}</td>
                <td>{row.mid == null ? DASH : formatPrice(row.mid)}</td>
                <td>{formatIv(row.iv)}</td>
                <td>{row.dte}</td>
                <td>{formatPctValue(row.distance_pct)}</td>
                <td>{formatCount(row.open_interest)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </DataTableFrame>
  );
}

function PlaybookCard({
  entry,
  cfdEntry,
  instrument,
}: {
  entry: PlaybookEntry;
  cfdEntry?: CfdPlaybookEntry;
  instrument?: string;
}) {
  const rows: { label: string; value: number | null; cfdValue: number | null | undefined; hint: string }[] = [
    { label: 'Trigger', value: entry.trigger, cfdValue: cfdEntry?.trigger, hint: entry.trigger_label },
    { label: 'Target', value: entry.target, cfdValue: cfdEntry?.target, hint: entry.target_label },
    { label: 'Invalidation', value: entry.invalidation, cfdValue: cfdEntry?.invalidation, hint: entry.invalidation_label },
  ];
  return (
    <section aria-label={entry.name} className="report-playbook-card">
      {/* T68: was h4 directly under "Playbook"'s h2 (the container above), skipping h3 --
          axe-core "heading-order". `.report-playbook-card__title` only ever sets margin/
          font-size, and h3's UA-default bold weight matches h4's, so retagging is visually
          a no-op. */}
      <h3 className="report-playbook-card__title">{entry.name}</h3>
      <dl className="report-playbook-card__rows">
        {rows.map((row) => (
          <div key={row.label} className="report-playbook-card__row">
            <dt className="report-playbook-card__label" title={row.hint}>
              {row.label}
            </dt>
            {/* A null is a real answer: no computed level sits there. Never a derived number. */}
            <dd className="report-playbook-card__value">
              {formatStrike(row.value)}
              {instrument && row.cfdValue != null && (
                <span className="report-muted-inline"> [{instrument} {formatStrike(row.cfdValue)}]</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <p className="report-playbook-card__strategy">{entry.strategy}</p>
    </section>
  );
}

// ---------------------------------------------------------------------------------------
// Full-text panel
// ---------------------------------------------------------------------------------------

/**
 * The collapsible "View full report" panel.
 *
 * The text is fetched from the backend's own `render_text` rather than assembled here, and
 * only once the panel is opened (`useReportText`'s `enabled`). Copying uses the async
 * clipboard API, which is unavailable over plain HTTP on a non-localhost origin and in jsdom;
 * the failure is caught and surfaced as a message rather than throwing into a click handler,
 * because a silently dead copy button is worse than one that says it did not work.
 */
function FullReportPanel({
  symbol,
  filter,
  cfdSpot,
}: {
  symbol: Underlying;
  filter: ExpiryFilter;
  cfdSpot?: number;
}) {
  const [open, setOpen] = useState(false);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const { data, isLoading, isError, error } = useReportText(symbol, filter, open, cfdSpot);

  const onCopy = useCallback(async () => {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(data);
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
  }, [data]);

  return (
    <Surface as="section" aria-label="Full report" className="report-fullreport" level="app" bordered={false} padded={false}>
      <button type="button" className="scan-toolbar__btn" aria-expanded={open} onClick={() => setOpen(true)}>
        View full report
      </button>
      {/* T122: an overlay rather than an inline expansion at the foot of the page. Always
          mounted -- DetailDrawer's caller contract -- so focus returns to the button. */}
      <DetailDrawer open={open} onClose={() => setOpen(false)} title={`${symbol} full report`} wide>
        {isLoading && <p aria-live="polite">Loading the full report…</p>}
        {isError && (
          <p role="alert">
            Could not load the full report: {error instanceof ApiError ? error.message : 'unexpected error'}
          </p>
        )}
        {data && (
          <>
            <div className="report-fulltext-actions">
              <button type="button" onClick={() => void onCopy()}>
                Copy to clipboard
              </button>
              <span aria-live="polite" className="report-fulltext-copy-status">
                {copyState === 'copied' ? 'Copied.' : copyState === 'failed' ? 'Copying is not available in this browser context.' : ''}
              </span>
            </div>
            <pre data-testid="report-text" className="report-fulltext">
              {data}
            </pre>
          </>
        )}
      </DetailDrawer>
    </Surface>
  );
}

// ---------------------------------------------------------------------------------------
// Empty state (T37)
// ---------------------------------------------------------------------------------------

/** A symbol with no snapshot yet. The expected state on first run and after
 * `docker compose down -v` — not a failure, and it must offer the one POST that fixes it.
 * Built on the shared `EmptyState` primitive (T64) rather than a bespoke section -- this is
 * exactly the pattern that component's own docstring says it generalized from. */
function NoDataYet({ symbol }: { symbol: Underlying }) {
  const capture = useCaptureSnapshot(symbol);
  return (
    <EmptyState
      heading={`No ${symbol} snapshot captured yet`}
      action={{
        label: 'Capture now',
        onClick: () => capture.mutate(),
        pending: capture.isPending,
        pendingLabel: 'Capturing…',
      }}
      errorMessage={
        capture.isError ? (
          <>Capture failed: {capture.error instanceof ApiError ? capture.error.message : 'unexpected error'}</>
        ) : undefined
      }
    >
      This is the normal state before the first capture. The end-of-day job runs at 16:20 ET
      on trading days, and a catch-up runs when the backend starts, so {symbol} will fill in
      on its own. You can also capture it now — it takes a couple of seconds.
    </EmptyState>
  );
}

// ---------------------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------------------

const REPORT_TABS = ['summary', 'gamma', 'premium', 'playbook', 'alerts'] as const;
type ReportTab = (typeof REPORT_TABS)[number];
const REPORT_TAB_LABELS: Record<ReportTab, string> = {
  summary: 'Summary',
  gamma: 'Gamma Exposure',
  premium: 'Premium Screen',
  playbook: 'Playbook',
  alerts: 'Risk Alerts',
};

/** T122: the positioning pill's tone. Long gamma dampens moves (green, the "stable" reading),
 * short gamma amplifies them (caution); a noise-dominated or empty scope has no direction and
 * stays neutral -- colouring it would imply a reading the page explicitly declines to give. */
function positioningTone(label: string): 'positive' | 'caution' | 'neutral' {
  if (label === 'LONG GAMMA') return 'positive';
  if (label === 'SHORT GAMMA') return 'caution';
  return 'neutral';
}

function ReportBody({
  report,
  symbol,
  filter,
  cfdSpot,
  theme,
  cfdControl,
}: {
  report: ReportData;
  symbol: Underlying;
  filter: ExpiryFilter;
  cfdSpot?: number;
  theme: 'light' | 'dark';
  /** The CFD-spot input, owned by `Report` (it reads the URL), rendered in the tab row. */
  cfdControl: ReactNode;
}) {
  const { positioning, ratios, levels, premium, playbook, alerts, summary, cfd } = report;
  const cfdPlaybookByKey = useMemo(() => {
    const map = new Map<string, CfdPlaybookEntry>();
    for (const entry of cfd?.playbook ?? []) map.set(entry.key, entry);
    return map;
  }, [cfd]);
  const cfdStraddlingByStrike = useMemo(() => cfdLevelIndex(cfd?.straddling), [cfd]);
  const cfdResistanceByStrike = useMemo(() => cfdLevelIndex(cfd?.resistance), [cfd]);
  const cfdSupportByStrike = useMemo(() => cfdLevelIndex(cfd?.support), [cfd]);

  // T122: only offer a tab whose section has something in it. Summary is always offered --
  // it holds the market structure and sentiment, which exist even when the backend sends no
  // summary lines; Risk alerts only when there are alerts.
  const tabDefs = useMemo(
    () =>
      REPORT_TABS.filter((t) => (t === 'alerts' ? alerts.length > 0 : true)).map(
        (value): TabDef<ReportTab> => ({
          value,
          label: REPORT_TAB_LABELS[value],
          count: value === 'alerts' ? alerts.length : null,
        }),
      ),
    [alerts.length],
  );
  const tabValues = useMemo(() => tabDefs.map((d) => d.value), [tabDefs]);
  const [tab, setTab] = useTabParam<ReportTab>('tab', tabValues);

  return (
    <>
      <Tabs
        label="Report sections"
        tabs={tabDefs}
        active={tab}
        onChange={setTab}
        sticky
        actions={cfdControl}
      >
      {/* T41: the CFD spot the user typed, the ratio it implies, and the honesty text that
          must travel with every converted number below -- shown once, at the top of whichever
          tab is open, rather than repeated section by section. */}
      {cfd && (
        <ScreeningNotice>
          {cfd.instrument} {formatPrice(cfd.cfd_spot)} / {symbol} {formatPrice(cfd.underlying_spot)} = ratio{' '}
          {cfd.ratio.toFixed(4)}. {cfd.note}
        </ScreeningNotice>
      )}

      {tab === 'summary' && (
        <>
          <div className="report-overview">
            <Surface as="section" aria-label="Market structure" className="report-structure" level="app">
              <h2 className="report-panel-title">Market structure</h2>
              <LevelRow
                heading="Resistance"
                tone="resistance"
                regionLabel="Top resistance levels"
                levels={levels.resistance}
                cfdIndex={cfdResistanceByStrike}
                instrument={cfd?.instrument}
                emptyMessage="No positive-gamma strike sits above spot in this expiry scope."
                theme={theme}
              />
              <LevelRow
                heading="Support"
                tone="support"
                regionLabel="Top support levels"
                levels={levels.support}
                cfdIndex={cfdSupportByStrike}
                instrument={cfd?.instrument}
                emptyMessage="No negative-gamma strike sits below spot in this expiry scope."
                theme={theme}
              />
              {/* A real market condition, labelled rather than sorted away. The example report
                  merged these into its support and resistance lists and ended up printing
                  support above resistance. */}
              {levels.overlapping && (
                <LevelRow
                  heading="Straddling spot"
                  tone="straddling"
                  regionLabel="Levels straddling spot"
                  levels={levels.straddling}
                  cfdIndex={cfdStraddlingByStrike}
                  instrument={cfd?.instrument}
                  note={levels.overlap_note}
                  emptyMessage=""
                  theme={theme}
                />
              )}
            </Surface>

            <Surface as="section" aria-label="Market sentiment" className="report-sentiment" level="app">
              <h2 className="report-panel-title">Market sentiment</h2>
              <span className="report-sentiment__value">
                {ratios.open_interest_ratio == null ? DASH : ratios.open_interest_ratio.toFixed(2)}
              </span>
              <span className="report-muted">
                Put/call ratio on open interest (volume {ratios.volume_ratio == null ? DASH : ratios.volume_ratio.toFixed(2)})
              </span>
              <span
                className={`report-positioning-pill report-positioning-pill--${positioningTone(positioning.label)}`}
                data-testid="positioning-label"
              >
                {positioning.label}
              </span>
              <span className="report-positioning-desc">{positioning.description}</span>
            </Surface>
          </div>

          <Surface as="section" aria-label="Executive summary" className="report-summary" level="app">
            <div className="report-summary__head">
              <h2 className="report-panel-title">Summary</h2>
              <FullReportPanel symbol={symbol} filter={filter} cfdSpot={cfdSpot} />
            </div>
            {summary.length > 0 && (
              <ul className="report-summary__lines">
                {summary.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </Surface>
        </>
      )}

      {tab === 'gamma' && (
        <Surface as="section" aria-label="Gamma exposure" className="report-section">
        <table>
          <tbody>
            <tr>
              <th scope="row">Net GEX</th>
              <td>{formatGex(positioning.net_gex)}</td>
            </tr>
            <tr>
              <th scope="row">Gross GEX</th>
              <td>{formatGex(positioning.abs_gex)}</td>
            </tr>
            <tr>
              <th scope="row">|Net| / gross</th>
              <td>
                {formatRatio(positioning.ratio)}
                <span className="report-muted-inline"> (floor {formatRatio(positioning.ratio_floor)})</span>
              </td>
            </tr>
            <tr>
              <th scope="row">Call wall</th>
              <td>
                {formatStrike(levels.call_wall)}
                {cfd?.call_wall?.strike != null && (
                  <span className="report-muted-inline"> [{cfd.instrument} {formatStrike(cfd.call_wall.strike)}]</span>
                )}
              </td>
            </tr>
            <tr>
              <th scope="row">Put wall</th>
              <td>
                {formatStrike(levels.put_wall)}
                {cfd?.put_wall?.strike != null && (
                  <span className="report-muted-inline"> [{cfd.instrument} {formatStrike(cfd.put_wall.strike)}]</span>
                )}
              </td>
            </tr>
            <tr>
              <th scope="row">Gamma flip</th>
              <td>
                {formatStrike(levels.flip_point)}
                <span className="report-muted-inline"> ({formatDistancePct(levels.flip_point, report.spot)})</span>
                {cfd?.flip_point?.strike != null && (
                  <span className="report-muted-inline"> [{cfd.instrument} {formatStrike(cfd.flip_point.strike)}]</span>
                )}
              </td>
            </tr>
          </tbody>
        </table>
        </Surface>
      )}

      {tab === 'premium' && (
        <Surface as="section" aria-label="Premium selling screen" className="report-section">
        <ScreeningNotice>
          Screening output computed from the current chain, not a recommendation. These are the
          quoted contracts sitting beyond the computed walls between {premium.dte_min} and{' '}
          {premium.dte_max} DTE. This app never routes an order.
        </ScreeningNotice>
        <CandidateTable
          rows={premium.calls}
          caption={`Calls at or above the ${formatStrike(premium.call_boundary)} call wall`}
        />
        <CandidateTable
          rows={premium.puts}
          caption={`Puts at or below the ${formatStrike(premium.put_boundary)} put wall`}
        />
        {premium.note && <p className="report-muted">{premium.note}</p>}
        </Surface>
      )}

      {tab === 'playbook' && (
        <Surface as="section" aria-label="Playbook" className="report-section">
        <ScreeningNotice>
          Screening output, not a recommendation. Every trigger, target and invalidation below
          is a level computed from this chain, or a dash where no computed level sits — none is
          a percentage of spot or a rule of thumb.
        </ScreeningNotice>
        {playbook.entries.length === 0 ? (
          <p className="report-muted">
            No scenarios: this expiry scope produced no walls to build them from.
          </p>
        ) : (
          <div className="report-playbook-cards">
            {playbook.entries.map((entry) => (
              <PlaybookCard
                key={entry.key}
                entry={entry}
                cfdEntry={cfdPlaybookByKey.get(entry.key)}
                instrument={cfd?.instrument}
              />
            ))}
          </div>
        )}
        {playbook.spot_in_range && (
          <p className="report-muted--spaced">
            Spot sits inside the {formatStrike(playbook.range_low)}–{formatStrike(playbook.range_high)} wall range, with
            open interest centred on {formatStrike(playbook.range_magnet)}.
          </p>
        )}
        </Surface>
      )}

      {tab === 'alerts' && (
          <Surface as="section" aria-label="Risk alerts" className="report-section" bordered={false} padded={false}>
            <ul className="report-alerts">
              {alerts.map((alert) => (
                <li key={alert.code}>
                  <strong>{alert.severity === 'WARNING' ? 'Warning' : 'Note'}:</strong> {alert.message}
                </li>
              ))}
            </ul>
          </Surface>
      )}
      </Tabs>
    </>
  );
}

export function Report() {
  // Same URL state as the dashboard and history pages, so `/report?symbol=GLD&filter=ALL`
  // deep-links and the nav carries the selection between pages. The symbol/expiry themselves
  // are now controlled only from `ContextBar` (T67) -- see this file's module docstring.
  const { symbol, filter, cfdSpot: cfdSpotRaw, setCfdSpot } = useDashboardParams();
  const { theme } = useTheme();

  // T41: parse and validate the typed CFD spot client-side, so a value in progress (empty, a
  // stray letter, a momentarily negative sign while typing "-5" the user is about to correct)
  // never gets sent to the API at all -- only a value that could possibly be right is. This is
  // deliberately stricter than the backend needs to be (it already rejects non-positive/
  // non-finite with a 422): sending a known-bad value would otherwise turn the *entire*
  // report into an error page over a field that is supposed to be an optional add-on.
  const cfdSpotParsed = cfdSpotRaw != null && cfdSpotRaw.trim() !== '' ? Number(cfdSpotRaw) : null;
  const cfdSpotInvalid = cfdSpotParsed != null && (!Number.isFinite(cfdSpotParsed) || cfdSpotParsed <= 0);
  const cfdSpotValid = cfdSpotParsed != null && !cfdSpotInvalid ? cfdSpotParsed : undefined;
  const cfdInstrument = CFD_INSTRUMENTS[symbol];

  const { data, isLoading, isError, error } = useReport(symbol, filter, cfdSpotValid);

  // T37: a 404 whose detail says there is no snapshot yet is an empty state, not a failure.
  // Anything else with a 404 (a missing Parquet file, say) is a genuine error and keeps the
  // error presentation — matching on the status alone would hide real breakage.
  const noDataYet = isError && error instanceof ApiError && error.status === 404 && (error.detail ?? '').includes('no snapshot captured yet');

  return (
    <div className="report-page">
      <PageHeader
        title={`${symbol} Analysis Results`}
        description="Options-intelligence report, key levels and playbook for the selected symbol -- screening output, not advice."
        caveat={
          data ? (
            <span data-testid="report-freshness">
              {formatFreshness(data.snapshot)}
              {data.snapshot.is_eod ? ' · EOD' : ''} · {data.filter}
            </span>
          ) : undefined
        }
        actions={data ? <HeadlineMetrics report={data} /> : undefined}
      />

      {cfdSpotInvalid && cfdInstrument && (
        <p role="alert" className="report-cfd-error">
          {cfdInstrument} spot must be a positive number — the CFD translation is off until this is fixed.
        </p>
      )}

      {isLoading && <p aria-live="polite">Loading the {symbol} report…</p>}

      {noDataYet && <NoDataYet symbol={symbol} />}

      {isError && !noDataYet && (
        <p role="alert">
          {/* `error.message` is the *parsed* detail, never the raw body — see api/client.ts. */}
          Could not load the {symbol} report: {error instanceof ApiError ? error.message : 'unexpected error'}
        </p>
      )}

      {data && (
        <ReportBody
          report={data}
          symbol={symbol}
          filter={filter}
          cfdSpot={cfdSpotValid}
          theme={theme}
          cfdControl={
            <div className="report-header-actions">
              {/* T41: the CFD instrument the user actually trades. Optional and URL-backed
                  (`?cfd=`), so `/report?symbol=GLD&cfd=4412.50` deep-links. Leaving it blank
                  leaves the report exactly as it is without T41 -- no converted block, no
                  placeholder. T47: `cfdInstrument` is `undefined` for a symbol `CFD_INSTRUMENTS`
                  does not cover (every sector/industry ETF today) -- the input degrades to "no
                  CFD mapping" by not rendering at all, mirroring `app/modules/gex/api/report.py`'s
                  degrade rather than showing a broken "undefined spot" label or an input that
                  would 422 if ever submitted. T122: moved from the page header to the tab row,
                  so the header's right side can carry the price and volatility. */}
              {cfdInstrument ? (
                <label>
                  {cfdInstrument} spot{' '}
                  <input
                    type="text"
                    inputMode="decimal"
                    placeholder="e.g. 4412.50"
                    aria-label={`${cfdInstrument} spot`}
                    value={cfdSpotRaw ?? ''}
                    onChange={(event) => setCfdSpot(event.target.value.length > 0 ? event.target.value : null)}
                  />
                </label>
              ) : (
                <span className="report-cfd-missing">No CFD mapping for {symbol}</span>
              )}
            </div>
          }
        />
      )}
    </div>
  );
}
