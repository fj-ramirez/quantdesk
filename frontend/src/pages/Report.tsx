/**
 * T40 — the options-intelligence report view, rendering T39's `GET /api/report/{underlying}`.
 *
 * Layout follows the screenshot the user supplied: a `{SYMBOL} Analysis Results` header, three
 * summary cards (price, volatility, sentiment), resistance and support level chips, then a
 * collapsible full-text panel. **Its numbers do not**: every figure here comes from our own
 * engine via T39, because the supplied example was reconciled against the very GLD chain it
 * claimed to describe and had the put/call ratio inverted, max pain wrong, IV wrong and its
 * support listed above its resistance. See `backend/app/gex/report.py`'s module docstring.
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
 */
import { useCallback, useMemo, useState } from 'react';
import { ApiError } from '../api/client';
import { useCaptureSnapshot, useReport, useReportText } from '../api/queries';
import {
  CFD_INSTRUMENTS,
  EXPIRY_FILTERS,
  EXPIRY_FILTER_LABELS,
  UNDERLYINGS,
  type CfdLevel,
  type CfdPlaybookEntry,
  type ExpiryFilter,
  type PlaybookEntry,
  type PremiumCandidate,
  type Report as ReportData,
  type ReportLevel,
  type Underlying,
} from '../api/types';
import {
  formatCount,
  formatDistancePct,
  formatGex,
  formatIv,
  formatPrice,
  formatStrike,
} from '../lib/format';
import { formatFreshness } from '../lib/time';
import { useDashboardParams } from '../state/urlState';
import { useTheme } from '../theme/ThemeContext';
import { vizPaletteFor, type VizPalette } from '../theme/vizPalette';

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

function Card({
  title,
  palette,
  children,
}: {
  title: string;
  palette: VizPalette;
  children: React.ReactNode;
}) {
  return (
    <section
      aria-label={title}
      style={{
        background: palette.surface,
        color: palette.textPrimary,
        border: `1px solid ${palette.gridline}`,
        borderRadius: 8,
        padding: 16,
        flex: '1 1 220px',
        minWidth: 220,
      }}
    >
      <h3 style={{ margin: '0 0 8px', fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.5, color: palette.textSecondary }}>
        {title}
      </h3>
      {children}
    </section>
  );
}

/**
 * One support or resistance chip.
 *
 * The colour is carried on the border and the dot only, never the text — see
 * `theme/vizPalette.ts` for why (the light-mode red is 3.85:1, below the 4.5:1 text
 * threshold) and for why red/green is safe here despite being the canonical CVD failure: the
 * signed distance and the section heading both restate the side in text, so hue is never the
 * only channel.
 */
/** `strike -> CfdLevel`, built once per render from a `CfdTranslation` side. `native_strike`
 * is the join key back to the `ReportLevel` it was translated from -- see
 * `backend/app/gex/report.py`'s `CfdLevel` docstring. */
function cfdLevelIndex(levels: CfdLevel[] | undefined): Map<number, CfdLevel> {
  const map = new Map<number, CfdLevel>();
  for (const level of levels ?? []) {
    if (level.native_strike != null) map.set(level.native_strike, level);
  }
  return map;
}

function LevelChip({ level, cfd, instrument, palette }: { level: ReportLevel; cfd?: CfdLevel; instrument?: string; palette: VizPalette }) {
  const color = level.side === 'SUPPORT' ? palette.levelSupport : level.side === 'RESISTANCE' ? palette.levelResistance : palette.textMuted;
  return (
    <li
      style={{
        display: 'inline-flex',
        alignItems: 'baseline',
        gap: 8,
        border: `1px solid ${color}`,
        borderRadius: 999,
        padding: '4px 12px',
        background: palette.surface,
        color: palette.textPrimary,
        listStyle: 'none',
      }}
    >
      <span
        aria-hidden="true"
        style={{ width: 8, height: 8, borderRadius: '50%', background: color, alignSelf: 'center' }}
      />
      <strong style={{ fontSize: 15 }}>{formatStrike(level.strike)}</strong>
      <span style={{ fontSize: 12, color: palette.textSecondary }}>{formatPctValue(level.distance_pct)}</span>
      <span style={{ fontSize: 12, color: palette.textMuted }}>{formatGex(level.net_gex)}</span>
      {/* T41: the same level in the CFD's terms, alongside the native strike -- never in its
          place. The percentage distance above is deliberately not repeated here: it is
          identical in both units by construction, so showing it twice would only invite the
          reader to wonder whether it should differ. */}
      {cfd && instrument && cfd.strike != null && (
        <span style={{ fontSize: 12, color: palette.textMuted }}>
          [{instrument} {formatStrike(cfd.strike)}]
        </span>
      )}
    </li>
  );
}

function LevelChips({
  heading,
  levels,
  cfdLevels,
  instrument,
  emptyMessage,
  palette,
}: {
  heading: string;
  levels: ReportLevel[];
  cfdLevels?: CfdLevel[];
  instrument?: string;
  emptyMessage: string;
  palette: VizPalette;
}) {
  const cfdIndex = useMemo(() => cfdLevelIndex(cfdLevels), [cfdLevels]);
  return (
    <section aria-label={heading} style={{ marginTop: 16 }}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14, color: palette.textPrimary }}>{heading}</h3>
      {levels.length === 0 ? (
        <p style={{ margin: 0, fontSize: 13, color: palette.textMuted }}>{emptyMessage}</p>
      ) : (
        <ul style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: 0, padding: 0 }}>
          {levels.map((level) => (
            <LevelChip
              key={`${level.side}-${level.strike}`}
              level={level}
              cfd={level.strike == null ? undefined : cfdIndex.get(level.strike)}
              instrument={instrument}
              palette={palette}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

/** The banner every trade-suggestion section carries. Wording is deliberate and is the same
 * sentence the backend's plain-text renderer prints: this is a screen over the current chain,
 * not advice, and the app never routes an order (PLAN.md's amended scope line). */
function ScreeningNotice({ palette, children }: { palette: VizPalette; children: React.ReactNode }) {
  return (
    <p
      style={{
        margin: '0 0 12px',
        fontSize: 12,
        color: palette.textSecondary,
        borderLeft: `3px solid ${palette.baseline}`,
        paddingLeft: 10,
      }}
    >
      {children}
    </p>
  );
}

function CandidateTable({ rows, palette, caption }: { rows: PremiumCandidate[]; palette: VizPalette; caption: string }) {
  if (rows.length === 0) {
    return (
      <p style={{ margin: '0 0 12px', fontSize: 13, color: palette.textMuted }}>
        {caption}: nothing quoted beyond this wall in the screened window.
      </p>
    );
  }
  return (
    <table style={{ width: '100%', marginBottom: 12 }}>
      <caption style={{ textAlign: 'left', fontSize: 13, color: palette.textSecondary, paddingBottom: 4 }}>{caption}</caption>
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
  );
}

function PlaybookCard({
  entry,
  cfdEntry,
  instrument,
  palette,
}: {
  entry: PlaybookEntry;
  cfdEntry?: CfdPlaybookEntry;
  instrument?: string;
  palette: VizPalette;
}) {
  const rows: { label: string; value: number | null; cfdValue: number | null | undefined; hint: string }[] = [
    { label: 'Trigger', value: entry.trigger, cfdValue: cfdEntry?.trigger, hint: entry.trigger_label },
    { label: 'Target', value: entry.target, cfdValue: cfdEntry?.target, hint: entry.target_label },
    { label: 'Invalidation', value: entry.invalidation, cfdValue: cfdEntry?.invalidation, hint: entry.invalidation_label },
  ];
  return (
    <section
      aria-label={entry.name}
      style={{ border: `1px solid ${palette.gridline}`, borderRadius: 8, padding: 12, flex: '1 1 240px' }}
    >
      <h4 style={{ margin: '0 0 8px', fontSize: 14 }}>{entry.name}</h4>
      <dl style={{ margin: 0, fontSize: 13 }}>
        {rows.map((row) => (
          <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '2px 0' }}>
            <dt style={{ color: palette.textSecondary }} title={row.hint}>
              {row.label}
            </dt>
            {/* A null is a real answer: no computed level sits there. Never a derived number. */}
            <dd style={{ margin: 0, fontVariantNumeric: 'tabular-nums' }}>
              {formatStrike(row.value)}
              {instrument && row.cfdValue != null && (
                <span style={{ marginLeft: 6, fontSize: 11, color: palette.textMuted }}>
                  [{instrument} {formatStrike(row.cfdValue)}]
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <p style={{ margin: '8px 0 0', fontSize: 12, color: palette.textMuted }}>{entry.strategy}</p>
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
  palette,
}: {
  symbol: Underlying;
  filter: ExpiryFilter;
  cfdSpot?: number;
  palette: VizPalette;
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
    <section aria-label="Full report" style={{ marginTop: 24 }}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((previous) => !previous)}
        style={{ fontSize: 14 }}
      >
        {open ? 'Hide full report' : 'View full report'}
      </button>

      {open && (
        <div style={{ marginTop: 12 }}>
          {isLoading && <p aria-live="polite">Loading the full report…</p>}
          {isError && (
            <p role="alert">
              Could not load the full report: {error instanceof ApiError ? error.message : 'unexpected error'}
            </p>
          )}
          {data && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                <button type="button" onClick={() => void onCopy()}>
                  Copy to clipboard
                </button>
                <span aria-live="polite" style={{ fontSize: 12, color: palette.textSecondary }}>
                  {copyState === 'copied' ? 'Copied.' : copyState === 'failed' ? 'Copying is not available in this browser context.' : ''}
                </span>
              </div>
              <pre
                data-testid="report-text"
                style={{
                  maxHeight: 420,
                  overflow: 'auto',
                  background: palette.surface,
                  color: palette.textPrimary,
                  border: `1px solid ${palette.gridline}`,
                  borderRadius: 6,
                  padding: 12,
                  fontSize: 12,
                  lineHeight: 1.45,
                  margin: 0,
                }}
              >
                {data}
              </pre>
            </>
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------
// Empty and error states (T37)
// ---------------------------------------------------------------------------------------

/** A symbol with no snapshot yet. The expected state on first run and after
 * `docker compose down -v` — not a failure, and it must offer the one POST that fixes it. */
function NoDataYet({ symbol, palette }: { symbol: Underlying; palette: VizPalette }) {
  const capture = useCaptureSnapshot(symbol);
  return (
    <section aria-label="No data yet" style={{ maxWidth: 560 }}>
      <h2 style={{ fontSize: 18 }}>No {symbol} snapshot captured yet</h2>
      <p style={{ color: palette.textSecondary, fontSize: 14 }}>
        This is the normal state before the first capture. The end-of-day job runs at 16:20 ET
        on trading days, and a catch-up runs when the backend starts, so {symbol} will fill in
        on its own. You can also capture it now — it takes a couple of seconds.
      </p>
      <button type="button" onClick={() => capture.mutate()} disabled={capture.isPending}>
        {capture.isPending ? 'Capturing…' : 'Capture now'}
      </button>
      {capture.isError && (
        <p role="alert" style={{ fontSize: 13 }}>
          Capture failed: {capture.error instanceof ApiError ? capture.error.message : 'unexpected error'}
        </p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------------------

function ReportBody({
  report,
  symbol,
  filter,
  cfdSpot,
  palette,
}: {
  report: ReportData;
  symbol: Underlying;
  filter: ExpiryFilter;
  cfdSpot?: number;
  palette: VizPalette;
}) {
  const { positioning, iv_regime: iv, ratios, levels, max_pain: maxPain, premium, playbook, alerts, summary, cfd } = report;
  const cfdPlaybookByKey = useMemo(() => {
    const map = new Map<string, CfdPlaybookEntry>();
    for (const entry of cfd?.playbook ?? []) map.set(entry.key, entry);
    return map;
  }, [cfd]);
  const cfdStraddlingByStrike = useMemo(() => cfdLevelIndex(cfd?.straddling), [cfd]);

  return (
    <>
      {/* T34: the data's honest age, above the analysis rather than below it. */}
      <p style={{ margin: '0 0 16px', fontSize: 12, color: palette.textMuted }} aria-live="polite">
        {formatFreshness(report.snapshot)}
        {report.snapshot.is_eod ? ' · EOD' : ''} · {report.filter}
      </p>

      {/* T41: the CFD spot the user typed, the ratio it implies, and the honesty text that
          must travel with every converted number below -- shown once, near the top, rather
          than repeated section by section. */}
      {cfd && (
        <p
          style={{
            margin: '0 0 16px',
            fontSize: 12,
            color: palette.textSecondary,
            borderLeft: `3px solid ${palette.baseline}`,
            paddingLeft: 10,
          }}
        >
          {cfd.instrument} {formatPrice(cfd.cfd_spot)} / {symbol} {formatPrice(cfd.underlying_spot)} = ratio{' '}
          {cfd.ratio.toFixed(4)}. {cfd.note}
        </p>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        <Card title="Current price" palette={palette}>
          <p style={{ margin: 0, fontSize: 26, fontVariantNumeric: 'tabular-nums' }}>{formatPrice(report.spot)}</p>
          {cfd && (
            <p style={{ margin: '2px 0 0', fontSize: 15, color: palette.textSecondary, fontVariantNumeric: 'tabular-nums' }}>
              {cfd.instrument} {formatPrice(cfd.cfd_spot)}
            </p>
          )}
          <p style={{ margin: '4px 0 0', fontSize: 13, color: palette.textSecondary }}>
            Max pain {formatStrike(maxPain.strike)} ({formatPctValue(maxPain.distance_pct)})
            {cfd?.max_pain?.strike != null && (
              <span style={{ color: palette.textMuted }}> [{cfd.instrument} {formatStrike(cfd.max_pain.strike)}]</span>
            )}
          </p>
        </Card>

        <Card title="Volatility" palette={palette}>
          <p style={{ margin: 0, fontSize: 26, fontVariantNumeric: 'tabular-nums' }}>{formatIv(iv.atm_iv)}</p>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: palette.textSecondary }}>
            ATM ~{iv.target_dte}d
            {iv.interpolated && iv.lower_dte != null && iv.upper_dte != null
              ? `, interpolated ${iv.lower_dte}–${iv.upper_dte} DTE`
              : ''}
          </p>
          {/* The label is null on a single-snapshot database and that is the correct answer,
              not a loading state. Never substitute a band. */}
          <p style={{ margin: '4px 0 0', fontSize: 13, color: iv.label ? palette.textPrimary : palette.textMuted }}>
            {iv.label ?? `Insufficient history — a regime label needs ${iv.min_history_required} prior snapshots, there are ${iv.history_observations}.`}
          </p>
        </Card>

        <Card title="Market sentiment" palette={palette}>
          <p style={{ margin: 0, fontSize: 26, fontVariantNumeric: 'tabular-nums' }}>
            {ratios.open_interest_ratio == null ? DASH : ratios.open_interest_ratio.toFixed(2)}
          </p>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: palette.textSecondary }}>
            Put/call ratio on open interest (volume {ratios.volume_ratio == null ? DASH : ratios.volume_ratio.toFixed(2)})
          </p>
          <p style={{ margin: '8px 0 0', fontSize: 14, fontWeight: 600 }} data-testid="positioning-label">
            {positioning.label}
          </p>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: palette.textSecondary }}>{positioning.description}</p>
        </Card>
      </div>

      <LevelChips
        heading="Top resistance levels"
        levels={levels.resistance}
        cfdLevels={cfd?.resistance}
        instrument={cfd?.instrument}
        emptyMessage="No positive-gamma strike sits above spot in this expiry scope."
        palette={palette}
      />
      <LevelChips
        heading="Top support levels"
        levels={levels.support}
        cfdLevels={cfd?.support}
        instrument={cfd?.instrument}
        emptyMessage="No negative-gamma strike sits below spot in this expiry scope."
        palette={palette}
      />

      {/* A real market condition, labelled rather than sorted away. The example report merged
          these into its support and resistance lists and ended up printing support above
          resistance. */}
      {levels.overlapping && (
        <section aria-label="Levels straddling spot" style={{ marginTop: 16 }}>
          <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Straddling spot</h3>
          <p style={{ margin: '0 0 8px', fontSize: 13, color: palette.textSecondary }}>{levels.overlap_note}</p>
          <ul style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: 0, padding: 0 }}>
            {levels.straddling.map((level) => (
              <LevelChip
                key={`straddle-${level.strike}`}
                level={level}
                cfd={level.strike == null ? undefined : cfdStraddlingByStrike.get(level.strike)}
                instrument={cfd?.instrument}
                palette={palette}
              />
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Gamma exposure" style={{ marginTop: 24 }}>
        <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Gamma exposure</h3>
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
                <span style={{ color: palette.textMuted }}> (floor {formatRatio(positioning.ratio_floor)})</span>
              </td>
            </tr>
            <tr>
              <th scope="row">Call wall</th>
              <td>
                {formatStrike(levels.call_wall)}
                {cfd?.call_wall?.strike != null && (
                  <span style={{ color: palette.textMuted }}> [{cfd.instrument} {formatStrike(cfd.call_wall.strike)}]</span>
                )}
              </td>
            </tr>
            <tr>
              <th scope="row">Put wall</th>
              <td>
                {formatStrike(levels.put_wall)}
                {cfd?.put_wall?.strike != null && (
                  <span style={{ color: palette.textMuted }}> [{cfd.instrument} {formatStrike(cfd.put_wall.strike)}]</span>
                )}
              </td>
            </tr>
            <tr>
              <th scope="row">Gamma flip</th>
              <td>
                {formatStrike(levels.flip_point)}
                <span style={{ color: palette.textMuted }}> ({formatDistancePct(levels.flip_point, report.spot)})</span>
                {cfd?.flip_point?.strike != null && (
                  <span style={{ color: palette.textMuted }}> [{cfd.instrument} {formatStrike(cfd.flip_point.strike)}]</span>
                )}
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section aria-label="Premium selling screen" style={{ marginTop: 24 }}>
        <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Premium selling screen</h3>
        <ScreeningNotice palette={palette}>
          Screening output computed from the current chain, not a recommendation. These are the
          quoted contracts sitting beyond the computed walls between {premium.dte_min} and{' '}
          {premium.dte_max} DTE. This app never routes an order.
        </ScreeningNotice>
        <CandidateTable
          rows={premium.calls}
          palette={palette}
          caption={`Calls at or above the ${formatStrike(premium.call_boundary)} call wall`}
        />
        <CandidateTable
          rows={premium.puts}
          palette={palette}
          caption={`Puts at or below the ${formatStrike(premium.put_boundary)} put wall`}
        />
        {premium.note && <p style={{ margin: 0, fontSize: 12, color: palette.textMuted }}>{premium.note}</p>}
      </section>

      <section aria-label="Playbook" style={{ marginTop: 24 }}>
        <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Playbook</h3>
        <ScreeningNotice palette={palette}>
          Screening output, not a recommendation. Every trigger, target and invalidation below
          is a level computed from this chain, or a dash where no computed level sits — none is
          a percentage of spot or a rule of thumb.
        </ScreeningNotice>
        {playbook.entries.length === 0 ? (
          <p style={{ margin: 0, fontSize: 13, color: palette.textMuted }}>
            No scenarios: this expiry scope produced no walls to build them from.
          </p>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
            {playbook.entries.map((entry) => (
              <PlaybookCard
                key={entry.key}
                entry={entry}
                cfdEntry={cfdPlaybookByKey.get(entry.key)}
                instrument={cfd?.instrument}
                palette={palette}
              />
            ))}
          </div>
        )}
        {playbook.spot_in_range && (
          <p style={{ margin: '8px 0 0', fontSize: 13, color: palette.textSecondary }}>
            Spot sits inside the {formatStrike(playbook.range_low)}–{formatStrike(playbook.range_high)} wall range, with
            open interest centred on {formatStrike(playbook.range_magnet)}.
          </p>
        )}
      </section>

      {alerts.length > 0 && (
        <section aria-label="Risk alerts" style={{ marginTop: 24 }}>
          <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Risk alerts</h3>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {alerts.map((alert) => (
              <li key={alert.code} style={{ fontSize: 13, marginBottom: 4 }}>
                <strong>{alert.severity === 'WARNING' ? 'Warning' : 'Note'}:</strong> {alert.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      {summary.length > 0 && (
        <section aria-label="Executive summary" style={{ marginTop: 24 }}>
          <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Summary</h3>
          {summary.map((line) => (
            <p key={line} style={{ margin: '0 0 8px', fontSize: 13, color: palette.textSecondary }}>
              {line}
            </p>
          ))}
        </section>
      )}

      <FullReportPanel symbol={symbol} filter={filter} cfdSpot={cfdSpot} palette={palette} />
    </>
  );
}

export function Report() {
  // Same URL state as the dashboard and history pages, so `/report?symbol=GLD&filter=ALL`
  // deep-links and the nav carries the selection between pages (AppShell forwards `search`).
  const { symbol, filter, cfdSpot: cfdSpotRaw, setSymbol, setFilter, setCfdSpot } = useDashboardParams();
  const { theme } = useTheme();
  const palette = vizPaletteFor(theme);

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
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: 12, marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>{symbol} Analysis Results</h2>
        <label style={{ fontSize: 13 }}>
          Symbol{' '}
          <select value={symbol} onChange={(event) => setSymbol(event.target.value as Underlying)}>
            {UNDERLYINGS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 13 }}>
          Expiries{' '}
          <select value={filter} onChange={(event) => setFilter(event.target.value as ExpiryFilter)}>
            {EXPIRY_FILTERS.map((value) => (
              <option key={value} value={value}>
                {EXPIRY_FILTER_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        {/* T41: the CFD instrument the user actually trades. Optional and URL-backed
            (`?cfd=`), so `/report?symbol=GLD&cfd=4412.50` deep-links. Leaving it blank leaves
            the report exactly as it is without T41 -- no converted block, no placeholder. */}
        <label style={{ fontSize: 13 }}>
          {cfdInstrument} spot{' '}
          <input
            type="text"
            inputMode="decimal"
            placeholder="e.g. 4412.50"
            aria-label={`${cfdInstrument} spot`}
            value={cfdSpotRaw ?? ''}
            onChange={(event) => setCfdSpot(event.target.value.length > 0 ? event.target.value : null)}
            style={{ width: 110 }}
          />
        </label>
      </div>

      {cfdSpotInvalid && (
        <p role="alert" style={{ fontSize: 12, color: palette.textSecondary, marginTop: -6 }}>
          {cfdInstrument} spot must be a positive number — the CFD translation is off until this is fixed.
        </p>
      )}

      {isLoading && <p aria-live="polite">Loading the {symbol} report…</p>}

      {noDataYet && <NoDataYet symbol={symbol} palette={palette} />}

      {isError && !noDataYet && (
        <p role="alert">
          {/* `error.message` is the *parsed* detail, never the raw body — see api/client.ts. */}
          Could not load the {symbol} report: {error instanceof ApiError ? error.message : 'unexpected error'}
        </p>
      )}

      {data && <ReportBody report={data} symbol={symbol} filter={filter} cfdSpot={cfdSpotValid} palette={palette} />}
    </div>
  );
}
