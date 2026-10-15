/**
 * The chart's URL state (T159): symbol, timeframe, the as-of instant and the layers shown.
 * In the URL so "XAUUSD M15 as of the 2026-10-09 open, with sweeps" is a link, and so a reload
 * lands on the same chart. Defaults are never written, which keeps `/broker` clean.
 */
import { useSearchParams } from 'react-router-dom';
import { TIMEFRAMES, type Timeframe } from '../api/types';

export const DEFAULT_SYMBOL = 'XAUUSD';
export const DEFAULT_TF: Timeframe = 'M15';

/** One toggle per family of things drawn on the candles. */
export const LAYERS = ['levels', 'zones', 'structure', 'breaks', 'sweeps', 'swings', 'patterns'] as const;
export type Layer = (typeof LAYERS)[number];
export const DEFAULT_LAYERS: readonly Layer[] = ['levels', 'zones', 'structure', 'breaks', 'sweeps'];

export const LAYER_LABELS: Record<Layer, string> = {
  levels: 'GEX levels',
  zones: 'Zones',
  structure: 'HH / HL / LH / LL',
  breaks: 'BOS / CHoCH',
  sweeps: 'Sweeps',
  swings: 'Swings',
  patterns: 'Bar patterns',
};

export interface ChartParamsPatch {
  symbol?: string;
  tf?: Timeframe;
  /** `null` clears it (back to now). */
  end?: string | null;
  layers?: readonly Layer[];
}

export interface ChartParams {
  symbol: string;
  tf: Timeframe;
  end: string | undefined;
  layers: ReadonlySet<Layer>;
  set: (patch: ChartParamsPatch) => void;
}

function isLayer(value: string): value is Layer {
  return (LAYERS as readonly string[]).includes(value);
}

function isTimeframe(value: string | null): value is Timeframe {
  return value != null && (TIMEFRAMES as readonly string[]).includes(value);
}

function parseLayers(raw: string | null): ReadonlySet<Layer> {
  if (raw == null) return new Set(DEFAULT_LAYERS);
  return new Set(raw.split(',').filter(isLayer));
}

export function useChartParams(): ChartParams {
  const [params, setParams] = useSearchParams();
  const tfRaw = params.get('tf');

  const set = (patch: ChartParamsPatch) => {
    const next = new URLSearchParams(params);
    const put = (key: string, value: string | undefined, fallback?: string) => {
      if (value == null || value === '' || value === fallback) next.delete(key);
      else next.set(key, value);
    };
    if (patch.symbol !== undefined) put('symbol', patch.symbol, DEFAULT_SYMBOL);
    if (patch.tf !== undefined) put('tf', patch.tf, DEFAULT_TF);
    if (patch.end !== undefined) put('end', patch.end ?? undefined);
    if (patch.layers !== undefined) {
      const ordered = LAYERS.filter((layer) => patch.layers?.includes(layer));
      const value = ordered.join(',');
      // Every layer off is a real choice and has to survive a reload, so it is written as an
      // empty value rather than dropped (dropped would mean "defaults").
      if (value === DEFAULT_LAYERS.join(',')) next.delete('layers');
      else next.set('layers', value);
    }
    // `replace`: stepping through bars must not fill the back button.
    setParams(next, { replace: true });
  };

  return {
    symbol: params.get('symbol') || DEFAULT_SYMBOL,
    tf: isTimeframe(tfRaw) ? tfRaw : DEFAULT_TF,
    end: params.get('end') || undefined,
    layers: parseLayers(params.get('layers')),
    set,
  };
}
