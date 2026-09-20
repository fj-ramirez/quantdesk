/**
 * The leaderboard's filter bar (T78).
 *
 * Options come from `GET /api/research/status`, not a hard-coded list, so a strategy family
 * added to `strategies.py` shows up the first cycle after it runs with no frontend change.
 *
 * The numeric filters default to `config/research.yaml`'s values, which is what makes the page
 * and the static HTML report show the same rows out of the box.
 */
import type { ResearchStatus } from '../api/types';

export interface FilterState {
  market: string;
  strategy: string;
  timeframe: string;
  minTradesOos: number;
  minExposure: number;
}

export const DEFAULT_FILTERS: FilterState = {
  market: '',
  strategy: '',
  timeframe: '',
  // research.yaml `filters:` — keep these in step with the backend defaults.
  minTradesOos: 20,
  minExposure: 0.02,
};

export interface FilterBarProps {
  value: FilterState;
  status: ResearchStatus | undefined;
  onChange: (next: FilterState) => void;
}

export function FilterBar({ value, status, onChange }: FilterBarProps) {
  const set = <K extends keyof FilterState>(key: K, next: FilterState[K]) =>
    onChange({ ...value, [key]: next });

  return (
    <div className="research-filters">
      <label className="research-filters__field">
        <span>Market</span>
        <select value={value.market} onChange={(e) => set('market', e.target.value)}>
          <option value="">All</option>
          {status?.markets.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </label>

      <label className="research-filters__field">
        <span>Strategy</span>
        <select value={value.strategy} onChange={(e) => set('strategy', e.target.value)}>
          <option value="">All</option>
          {status?.strategies.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>

      <label className="research-filters__field">
        <span>Timeframe</span>
        <select value={value.timeframe} onChange={(e) => set('timeframe', e.target.value)}>
          <option value="">All</option>
          {status?.timeframes.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>

      <label className="research-filters__field">
        <span>Min OOS fills</span>
        <input
          type="number"
          min={0}
          value={value.minTradesOos}
          onChange={(e) => set('minTradesOos', Number(e.target.value))}
        />
      </label>

      <label className="research-filters__field">
        <span>Min exposure</span>
        <input
          type="number"
          min={0}
          max={1}
          step={0.01}
          value={value.minExposure}
          onChange={(e) => set('minExposure', Number(e.target.value))}
        />
      </label>

      <button
        type="button"
        className="research-filters__reset"
        onClick={() => onChange(DEFAULT_FILTERS)}
      >
        Reset
      </button>
    </div>
  );
}
