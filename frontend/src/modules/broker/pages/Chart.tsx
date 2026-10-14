/**
 * `/broker`: the CFD chart (T159). The broker's own prices with the desk's analysis on them:
 * price-action events from `scan/price_action.py` and the GEX levels translated into CFD price
 * with a measured basis.
 *
 * **As-of is the replay control.** `end` in the URL redraws everything (candles, events, zones,
 * levels) as it was knowable at that instant, and the step buttons move it one bar at a time.
 * A historical chart is flagged the same way the terminal flags one, because a replay that
 * reads as live is the expensive mistake.
 */
import { useCallback, useMemo, useState } from 'react';
import { PageHeader } from '../../../components/ui/PageHeader';
import { SegmentedControl, Toolbar } from '../../../components/ui/Toolbar';
import { Surface } from '../../../components/ui/Surface';
import { ApiError } from '../../../lib/http';
import { formatNyDateTime, toIsoInstant, toLocalInput } from '../../../lib/time';
import { useBrokerChart, useBrokerSymbols } from '../api/queries';
import { TIMEFRAMES, type ChartEvent, type ChartResponse, type Timeframe } from '../api/types';
import { KIND_LAYER } from '../components/chartData';
import { PriceChart } from '../components/PriceChart';
import { LAYERS, LAYER_LABELS, useChartParams, type Layer } from '../state/chartParams';

const TF_MINUTES: Record<Timeframe, number> = { M1: 1, M5: 5, M15: 15, M30: 30, H1: 60, H4: 240, D1: 1440 };

const KIND_NAME: Record<ChartEvent['kind'], string> = {
  swing_high: 'Swing high',
  swing_low: 'Swing low',
  HH: 'Higher high',
  LH: 'Lower high',
  HL: 'Higher low',
  LL: 'Lower low',
  bos: 'Break of structure',
  choch: 'Change of character',
  sweep: 'Liquidity sweep',
  pin: 'Pin bar',
  engulfing: 'Engulfing',
  inside: 'Inside bar',
  outside: 'Outside bar',
};

function formatPx(value: number | null | undefined): string {
  if (value == null) return '·';
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** The newest bar that had closed. Steps are measured from it. */
function lastClosed(data: ChartResponse | undefined) {
  if (!data) return undefined;
  for (let i = data.bars.length - 1; i >= 0; i -= 1) if (!data.bars[i].forming) return data.bars[i];
  return undefined;
}

function Replay({ data, tf, end, onEnd }: {
  data: ChartResponse | undefined;
  tf: Timeframe;
  end: string | undefined;
  onEnd: (next: string | null) => void;
}) {
  const closed = lastClosed(data);
  const historical = end != null;

  const back = () => {
    // Ending the chart at the last closed bar's open drops that bar, so the one before it
    // becomes the newest closed bar: one bar back, whatever the gaps between them.
    if (closed) onEnd(closed.time);
  };
  const forward = () => {
    if (!closed) return;
    const next = Date.parse(closed.time) + 2 * TF_MINUTES[tf] * 60_000;
    onEnd(next >= Date.now() ? null : new Date(next).toISOString());
  };

  return (
    <div className={`asof${historical ? ' asof--historical' : ''}`}>
      <label className="asof__field">
        <span className="asof__label">As of</span>
        <input
          type="datetime-local"
          value={toLocalInput(end)}
          onChange={(e) => onEnd(toIsoInstant(e.target.value) ?? null)}
          aria-label="As of"
        />
      </label>
      <button type="button" className="asof__clear" onClick={back} disabled={!closed} aria-label="One bar back">
        ◀ bar
      </button>
      <button type="button" className="asof__clear" onClick={forward} disabled={!historical || !closed} aria-label="One bar forward">
        bar ▶
      </button>
      {historical ? (
        <>
          <span className="asof__badge" role="status">
            Replay: only what was knowable at {formatNyDateTime(end)}
          </span>
          <button type="button" className="asof__clear" onClick={() => onEnd(null)}>
            Back to live
          </button>
        </>
      ) : (
        <span className="asof__badge asof__badge--live" role="status">
          Live, refreshed every minute
        </span>
      )}
    </div>
  );
}

function LayerToggles({ layers, onChange }: { layers: ReadonlySet<Layer>; onChange: (next: Layer[]) => void }) {
  return (
    <fieldset className="broker-layers">
      <legend className="broker-layers__legend">Show</legend>
      {LAYERS.map((layer) => (
        <label key={layer} className="broker-layers__item">
          <input
            type="checkbox"
            checked={layers.has(layer)}
            onChange={(e) => {
              const next = new Set(layers);
              if (e.target.checked) next.add(layer);
              else next.delete(layer);
              onChange([...next]);
            }}
          />
          {LAYER_LABELS[layer]}
        </label>
      ))}
    </fieldset>
  );
}

function Context({ data, layers }: { data: ChartResponse; layers: ReadonlySet<Layer> }) {
  const recent = data.events.filter((e) => layers.has(KIND_LAYER[e.kind])).slice(-10).reverse();
  const level = data.levels.length > 0 ? data.levels[data.levels.length - 1] : null;
  const forming = data.bars.length > 0 && data.bars[data.bars.length - 1].forming;

  return (
    <div className="broker-context">
      <Surface level="raised" bordered padded as="section" aria-label="GEX levels">
        <h2 className="broker-context__title">GEX levels in {data.symbol}</h2>
        {data.pair == null ? (
          <p className="broker-context__note">No desk pair: nothing on the desk measures this CFD's options.</p>
        ) : level == null ? (
          <p className="broker-context__note">
            No {data.pair.desk} snapshot in this window. Levels are drawn only from snapshots
            captured at or before each bar.
          </p>
        ) : (
          <>
            <dl className="broker-context__levels">
              <dt>Call wall</dt><dd>{formatPx(level.call_wall)}</dd>
              <dt>Flip</dt><dd>{formatPx(level.flip_point)}</dd>
              <dt>Put wall</dt><dd>{formatPx(level.put_wall)}</dd>
            </dl>
            <p className="broker-context__note">
              {data.pair.desk} snapshot {formatNyDateTime(level.captured_at)}, translated by{' '}
              {data.pair.method}
              {data.level_basis ? ` (${data.level_basis.basis_kind} basis at ${formatNyDateTime(data.level_basis.basis_at)})` : ''}.
            </p>
            {data.pair.proxy && (
              <p className="broker-context__warn">
                Proxy: {data.symbol} tracks a different index than {data.pair.desk}, so these
                levels are approximate.
              </p>
            )}
            {data.level_basis?.roll_after_basis && (
              <p className="broker-context__warn">
                A futures roll happened after this basis was measured. The levels are off by the
                roll until the next basis.
              </p>
            )}
          </>
        )}
      </Surface>

      <Surface level="raised" bordered padded as="section" aria-label="Recent events">
        <h2 className="broker-context__title">Recent events</h2>
        {recent.length === 0 ? (
          <p className="broker-context__note">None in the layers shown.</p>
        ) : (
          <ul className="broker-events">
            {recent.map((e) => (
              <li key={`${e.kind}-${e.time}-${e.confirmed_time}`} className={`broker-events__item broker-events__item--${e.direction ?? 'none'}`}>
                <span className="broker-events__kind">{KIND_NAME[e.kind]}</span>
                <span className="broker-events__px">{formatPx(e.price)}</span>
                <span className="broker-events__time">confirmed {formatNyDateTime(e.confirmed_time)}</span>
              </li>
            ))}
          </ul>
        )}
      </Surface>

      <Surface level="raised" bordered padded as="section" aria-label="Data">
        <h2 className="broker-context__title">Data</h2>
        <p className="broker-context__note">
          M1 bars through {data.last_bar_at ? formatNyDateTime(data.last_bar_at) : '·'}, resampled
          on the broker's server clock, so D1 is the New York 17:00 session.
        </p>
        {forming && (
          <p className="broker-context__note">
            The last bar is still forming. It is drawn muted, and no detector has seen it.
          </p>
        )}
        <p className="broker-context__note">
          Events are placed on the bar they are about and were only knowable at their confirmation
          bar. A swing is confirmed three bars after its extreme.
        </p>
      </Surface>
    </div>
  );
}

export function Chart() {
  const { symbol, tf, end, layers, set } = useChartParams();
  const symbols = useBrokerSymbols();
  const chart = useBrokerChart({ symbol, tf, end });
  const [markerStats, setMarkerStats] = useState<{ drawn: number; dropped: number } | null>(null);
  const onMarkers = useCallback((drawn: number, dropped: number) => setMarkerStats({ drawn, dropped }), []);

  const symbolOptions = useMemo(() => {
    const listed = symbols.data?.map((s) => s.symbol) ?? [];
    return listed.includes(symbol) ? listed : [symbol, ...listed];
  }, [symbols.data, symbol]);

  const data = chart.data;
  const notFound = chart.error instanceof ApiError && chart.error.status === 404;

  return (
    <div className="broker-page">
      <PageHeader
        title="Chart"
        description="The broker's own prices, with price action and the desk's GEX levels drawn in CFD price."
        caveat="Price-action events are descriptions, not signals: none of the 288 tested variants passed stage 1 (T147)."
      />

      <Toolbar className="broker-toolbar">
        <label className="broker-toolbar__symbol">
          <span>Symbol</span>
          <select value={symbol} onChange={(e) => set({ symbol: e.target.value })} aria-label="Symbol">
            {symbolOptions.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <SegmentedControl
          label="Timeframe"
          values={TIMEFRAMES}
          active={tf}
          render={(v) => v}
          onChange={(v) => set({ tf: v })}
        />
      </Toolbar>

      <Replay data={data} tf={tf} end={end} onEnd={(next) => set({ end: next })} />
      <LayerToggles layers={layers} onChange={(next) => set({ layers: next })} />

      {chart.isPending ? (
        <div className="broker-loading">Loading {symbol}…</div>
      ) : notFound ? (
        <Surface level="raised" bordered padded>
          <p>{symbol} is not a configured broker symbol.</p>
        </Surface>
      ) : chart.isError || !data ? (
        <Surface level="raised" bordered padded>
          <p>The chart could not load. {chart.error instanceof ApiError ? chart.error.message : ''}</p>
        </Surface>
      ) : data.bars.length === 0 ? (
        <Surface level="raised" bordered padded>
          <p>
            No {symbol} bars {end ? `before ${formatNyDateTime(end)}` : 'yet'}. The broker-ingest
            worker writes M1 bars while the MT5 terminal is connected.
          </p>
        </Surface>
      ) : (
        <div className="broker-workspace">
          <Surface level="raised" bordered className="broker-workspace__chart">
            <PriceChart data={data} layers={layers} fitKey={`${symbol}|${tf}`} onMarkers={onMarkers} />
            {markerStats != null && markerStats.dropped > 0 && (
              <p className="broker-context__warn" role="alert">
                {markerStats.dropped} event markers matched no bar and were not drawn.
              </p>
            )}
          </Surface>
          <Context data={data} layers={layers} />
        </div>
      )}
    </div>
  );
}
