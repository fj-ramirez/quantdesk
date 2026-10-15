/**
 * The launcher's conditions line (T172): the synthesis label and the sentiment score, linking to
 * the terminal's Regime page where every input behind them is shown.
 *
 * Same rule as `LauncherTape`: real numbers or nothing. It renders nothing while loading, on
 * error, or when there is neither a synthesis nor a score, so a fresh install's launcher does not
 * show a broken tile.
 */
import { Link } from 'react-router-dom';
import { useConditions } from '../modules/terminal/api/queries';
import { BAND_TEXT, SYNTHESIS_TEXT } from '../modules/terminal/components/conditionsText';

export function LauncherConditions() {
  const { data } = useConditions(undefined);
  if (!data) return null;
  const { synthesis, sentiment } = data;
  if (!synthesis && sentiment.score == null) return null;

  return (
    <Link className="lx-conditions" to="/terminal/regime" aria-label="Market conditions">
      <span className="lx-conditions__caption">Conditions</span>
      <span className={`lx-conditions__label lx-conditions__label--${synthesis ?? 'none'}`}>
        {synthesis ? (SYNTHESIS_TEXT[synthesis] ?? synthesis) : 'No synthesis'}
      </span>
      {sentiment.score != null && sentiment.band && (
        <span className="lx-conditions__score">
          Sentiment {sentiment.score.toFixed(0)} · {BAND_TEXT[sentiment.band] ?? sentiment.band}
        </span>
      )}
    </Link>
  );
}
