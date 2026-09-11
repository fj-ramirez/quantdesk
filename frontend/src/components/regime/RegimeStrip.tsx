/**
 * T54 — the cross-asset regime strip: one row of tiles answering "what kind of tape is it for
 * everything at once" (`plans/continuation/06-cross-asset-regime.md`). Reads
 * `GET /api/scan/cross-asset` (`useCrossAsset`) and renders nine tiles:
 *
 * `VIX9D/VIX | VIX/VIX3M | VVIX | VIX 1y pct | SPY VRP | Sector corr 20d | UUP 20d | GLD 20d |
 * TLT 20d` — the exact column list `07-ui.md`'s `RegimeStrip` section names. Built on T55's
 * shared kit (`StatusChip`, `PercentileBar`), neither of which is modified here.
 *
 * **This component is not wired into any page.** `/regime` and `/scan` are owned by other
 * tasks (T49, T44/T56) — importing and placing `<RegimeStrip />` on either page is left to
 * whoever lands next, per this task's own brief.
 *
 * **No composite score, ever.** Every tile is independent (plan 06: "Six [nine] tiles with
 * rules are more useful than one number and cannot hide a bad input") — nothing in this file
 * combines two tiles' values into a third number. The one place two tiles' *labels* interact
 * is the term-structure tooltip's one extra clause for contango-with-low-VVIX (see
 * `_termStructureTooltip` below) — plan 06's own named example of how far a tooltip may go
 * ("fade-friendly historically") and no further.
 *
 * **Labels are rules, not opinions.** `term_structure` (`contango`/`backwardation`/`mixed`) is
 * the API's own rule-based call (`app.scan.cross_asset.term_structure`), rendered verbatim.
 * Every other tile's `StatusChip` label is a plain, stated rule over that tile's own number
 * (a percentile band, a correlation band, or a sign/flat call on a 20-day return) — see each
 * tile's own tooltip for the exact rule, never a fabricated opinion.
 *
 * **`n/a` tiles.** Every value that can be `null` renders the literal text `"n/a"` (matching
 * `StatusChip`'s own `"n/a"` convention for a `null` status, so the strip reads consistently)
 * with the reason in the `title` tooltip — the API's own `*_reason` field where one exists
 * (`term_structure_reason`, `vrp_reason`), or this component's own honest description of the
 * `*_n`/sample-size field otherwise. Never a fabricated number, per `percentile_252`'s own
 * "fewer than 60 bars returns `None`" contract and the plan's "sector correlation... report the
 * effective sample size" requirement (the sector-correlation tile always shows its own
 * `sector_correlation_n` next to the value, not only when the value itself is `null`).
 *
 * **T69 — `compact` (additive, Overview-only).** `/regime` (`Regime.tsx`) calls `<RegimeStrip />`
 * with no prop and keeps rendering all nine tiles unconditionally, exactly as before this task
 * — that page's own reason to exist is the complete reference strip, so it is deliberately
 * never abbreviated. Overview passes `compact`, which splits the same nine tiles (no value,
 * ranking, or endpoint changed) into:
 *  - **Primary (always visible):** VIX9D/VIX, VIX/VIX3M, VVIX, SPY VRP — the vol/term-structure
 *    cluster. Judgment call, not a fixed spec: these four answer "what kind of tape is it"
 *    most directly (plan 06's own framing for this whole component) and change intraday, so
 *    they are the ones worth first-screen space on a page that is itself a cross-market
 *    morning brief, not the regime board.
 *  - **Secondary (behind a native `<details>`, collapsed by default):** VIX 1y pct, Sector
 *    corr 20d, UUP 20d, GLD 20d, TLT 20d — valuable reference material (per the user's own
 *    review note) but slower-moving cross-asset context rather than tape-driving signal, so
 *    they move behind a disclosure instead of consuming another full row on first load. Same
 *    `<details>`/`<summary>` pattern Rotation's "About this chart" already uses — no bespoke
 *    focus-trap logic, it is natively keyboard-operable.
 */
import { useCrossAsset } from '../../api/queries';
import { PercentileBar } from '../scan/PercentileBar';
import { StatusChip } from '../scan/StatusChip';
import type { CrossAssetResponse } from '../../api/types';
import { formatRatio, formatSignedPct } from '../../lib/format';

const EMPTY = 'n/a';

function formatVolPoints(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return EMPTY;
  return value.toFixed(2);
}

function formatSignedVolPoints(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return EMPTY;
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)} pts`;
}

function formatPctValue(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return EMPTY;
  return `${Math.round(value * 100)}%`;
}

function formatCorrelation(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return EMPTY;
  return value.toFixed(2);
}

function formatRatioValue(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return EMPTY;
  return formatRatio(value);
}

function formatMove(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return EMPTY;
  return formatSignedPct(value);
}

/** A percentile-banded label: a rule about the number itself (plan 06's "Labels are rules,
 * not opinions"), not a judgment about whether the level is good or bad. `null` when there is
 * no percentile to band (short history, or this tile never computes one). */
function bandLabel(pct: number | null): string | null {
  if (pct == null || !Number.isFinite(pct)) return null;
  if (pct < 0.33) return 'low';
  if (pct > 0.66) return 'elevated';
  return 'normal';
}

/** Sector correlation's own band, over the correlation value directly (there is no rolling
 * percentile history computed for it — see `app.scan.cross_asset`'s module docstring for why
 * that was scoped out of this task). `|corr| < 0.3` "low", `< 0.6` "moderate", else "high". */
function correlationBandLabel(value: number | null): string | null {
  if (value == null || !Number.isFinite(value)) return null;
  const abs = Math.abs(value);
  if (abs < 0.3) return 'low';
  if (abs < 0.6) return 'moderate';
  return 'high';
}

/** A 20-day move's sign, with a small dead zone so a genuinely flat move (e.g. TLT sitting
 * still) reads as "flat" rather than an arbitrary "up"/"down" on noise. */
function moveBandLabel(value: number | null): string | null {
  if (value == null || !Number.isFinite(value)) return null;
  if (Math.abs(value) < 0.002) return 'flat';
  return value > 0 ? 'up' : 'down';
}

const TERM_STRUCTURE_RULE =
  'Plain close ratios: contango when VIX/VIX3M < 1 and VIX9D/VIX < 1; backwardation when both ' +
  '> 1; mixed otherwise.';

/** The one place two tiles' state combines into a tooltip clause — plan 06's own named,
 * bounded example, and no further: "The tooltip may say 'fade-friendly historically' for
 * contango with low VVIX, and the strip must not go further than that." This never changes
 * `term_structure` itself, never produces a number, and appears only in the tooltip text. */
function termStructureTooltip(data: CrossAssetResponse): string {
  if (data.term_structure === null) {
    return `${TERM_STRUCTURE_RULE} n/a: ${data.term_structure_reason ?? 'missing an input close'}.`;
  }
  let text = `${TERM_STRUCTURE_RULE} Currently ${data.term_structure}.`;
  if (data.term_structure === 'contango' && bandLabel(data.vvix_pct) === 'low') {
    text += ' Contango with low VVIX: fade-friendly historically.';
  }
  return text;
}

interface TileProps {
  label: string;
  value: string;
  tooltip: string;
  /** Omit entirely (not `null`) to render no percentile bar at all — this tile never computes
   * one (sector correlation, the three 20-day moves). Pass `null` when a percentile *would*
   * apply but isn't available yet (short history) — `PercentileBar` renders its own dash. */
  pct?: number | null;
  /** A small line under the value/bar — used only by the sector-correlation tile, to show its
   * effective sample size unconditionally (plan 06's "report the effective sample size"). */
  footnote?: string;
  chip?: { label: string | null; tooltip: string };
}

function Tile({ label, value, tooltip, pct, footnote, chip }: TileProps) {
  const isEmpty = value === EMPTY;
  return (
    <div className="regime-strip__tile">
      <span className="regime-strip__label">{label}</span>
      <span
        className={isEmpty ? 'regime-strip__value regime-strip__value--empty' : 'regime-strip__value'}
        title={tooltip}
      >
        {value}
      </span>
      {pct !== undefined && <PercentileBar value={pct} />}
      {footnote && <span className="regime-strip__label">{footnote}</span>}
      {chip && (
        <span title={chip.tooltip}>
          <StatusChip status={chip.label} />
        </span>
      )}
    </div>
  );
}

export interface RegimeStripProps {
  /** T69, additive: default `false` renders the complete nine-tile strip unchanged (`/regime`
   * never passes this). `true` (Overview only) shows the primary vol/term-structure cluster
   * up front and puts the remaining five tiles behind a collapsed `<details>` — see this
   * file's own docstring for which tiles are primary and why. */
  compact?: boolean;
}

export function RegimeStrip({ compact = false }: RegimeStripProps = {}) {
  const { data, isLoading, isError } = useCrossAsset();

  if (isLoading) {
    return (
      <section className="regime-strip" aria-label="Cross-asset regime strip" aria-live="polite">
        Loading regime strip…
      </section>
    );
  }

  if (isError || !data) {
    return (
      <section className="regime-strip" aria-label="Cross-asset regime strip" aria-live="polite">
        Regime strip unavailable
      </section>
    );
  }

  const tsTooltip = termStructureTooltip(data);
  const tsChip = { label: data.term_structure, tooltip: tsTooltip };

  // Built once, in the original nine-tile order, and reused by both branches below so the
  // default (non-compact) strip stays byte-identical to before this task -- compact mode only
  // regroups the same elements, it never reorders or duplicates a value.
  const vix9dVix = (
    <Tile
      key="vix9d-vix"
      label="VIX9D/VIX"
      value={formatRatioValue(data.vix9d_vix_ratio)}
      tooltip={tsTooltip}
      pct={data.vix9d_vix_ratio_pct}
      chip={tsChip}
    />
  );
  const vixVix3m = (
    <Tile
      key="vix-vix3m"
      label="VIX/VIX3M"
      value={formatRatioValue(data.vix_vix3m_ratio)}
      tooltip={tsTooltip}
      pct={data.vix_vix3m_ratio_pct}
      chip={tsChip}
    />
  );
  const vvix = (
    <Tile
      key="vvix"
      label="VVIX"
      value={formatVolPoints(data.vvix)}
      tooltip="Cboe's VVIX (vol-of-vol on VIX options)."
      pct={data.vvix_pct}
      chip={{
        label: bandLabel(data.vvix_pct),
        tooltip: 'Banded on VVIX’s own trailing 1-year percentile: low (<33rd), normal, elevated (>66th).',
      }}
    />
  );
  const vix1yPct = (
    <Tile
      key="vix-1y-pct"
      label="VIX 1y pct"
      value={formatPctValue(data.vix_pct)}
      tooltip={`VIX's own percentile rank within its trailing 252-bar (~1-year) history. n=${data.vix_pct_n} bars (needs >= 60).`}
      pct={data.vix_pct}
      chip={{
        label: bandLabel(data.vix_pct),
        tooltip: 'Banded on the percentile itself: low (<33rd), normal, elevated (>66th).',
      }}
    />
  );
  const spyVrp = (
    <Tile
      key="spy-vrp"
      label="SPY VRP"
      value={formatSignedVolPoints(data.vrp)}
      tooltip={
        data.vrp_reason
          ? `VIX minus SPY's 20-day realized vol, both in vol points. n/a: ${data.vrp_reason}.`
          : "VIX minus SPY's 20-day realized vol, both in vol points (annualized). Positive means options are pricing more vol than has recently realized."
      }
      pct={data.vrp_pct}
      chip={{
        label: bandLabel(data.vrp_pct),
        tooltip: "Banded on VRP's own trailing 1-year percentile: low (<33rd), normal, elevated (>66th).",
      }}
    />
  );
  const sectorCorr = (
    <Tile
      key="sector-corr"
      label="Sector corr 20d"
      value={formatCorrelation(data.sector_correlation)}
      tooltip="Mean pairwise 20-day correlation of daily log returns across the 11 sector ETFs."
      footnote={`n=${data.sector_correlation_n}/20 aligned days`}
      chip={{
        label: correlationBandLabel(data.sector_correlation),
        tooltip: 'Banded on the correlation value itself: low (<0.3), moderate (<0.6), high (>=0.6).',
      }}
    />
  );
  const uup = (
    <Tile
      key="uup"
      label="UUP 20d"
      value={formatMove(data.uup_return_20d)}
      tooltip="20-day close-to-close return on UUP (dollar index proxy)."
      chip={{
        label: moveBandLabel(data.uup_return_20d),
        tooltip: 'Flat when |return| < 0.2%; otherwise the sign of the 20-day return.',
      }}
    />
  );
  const gld = (
    <Tile
      key="gld"
      label="GLD 20d"
      value={formatMove(data.gld_return_20d)}
      tooltip="20-day close-to-close return on GLD."
      chip={{
        label: moveBandLabel(data.gld_return_20d),
        tooltip: 'Flat when |return| < 0.2%; otherwise the sign of the 20-day return.',
      }}
    />
  );
  const tlt = (
    <Tile
      key="tlt"
      label="TLT 20d"
      value={formatMove(data.tlt_return_20d)}
      tooltip="20-day close-to-close return on TLT (rates proxy)."
      chip={{
        label: moveBandLabel(data.tlt_return_20d),
        tooltip: 'Flat when |return| < 0.2%; otherwise the sign of the 20-day return.',
      }}
    />
  );

  if (!compact) {
    return (
      <section className="regime-strip" aria-label="Cross-asset regime strip">
        {vix9dVix}
        {vixVix3m}
        {vvix}
        {vix1yPct}
        {spyVrp}
        {sectorCorr}
        {uup}
        {gld}
        {tlt}
      </section>
    );
  }

  return (
    <section className="regime-strip regime-strip--compact" aria-label="Cross-asset regime strip">
      <div className="regime-strip__primary">
        {vix9dVix}
        {vixVix3m}
        {vvix}
        {spyVrp}
      </div>
      <details className="regime-strip__more">
        <summary>5 more regime tiles</summary>
        <div className="regime-strip__secondary">
          {vix1yPct}
          {sectorCorr}
          {uup}
          {gld}
          {tlt}
        </div>
      </details>
    </section>
  );
}
