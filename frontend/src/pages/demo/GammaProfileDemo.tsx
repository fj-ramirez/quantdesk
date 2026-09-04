/**
 * Standalone stories-style demo for T14's two deliverables (GammaProfile, KeyLevels),
 * mounted at its own route (`/demo/gamma-profile`) rather than in `src/pages/Dashboard.tsx`
 * — T16 owns dashboard assembly and another agent is editing sibling components there right
 * now, so wiring these in here would collide with that work.
 *
 * "Ex-0DTE" isn't a field on `GammaProfilePoint` (T14 GUESS in api/types.ts only carries
 * `{ spot, total_gex }` for whatever `filter` was requested) — instead this page fetches the
 * same `GexResult` twice, once per `ExpiryFilter`, and hands each response's `profile` array
 * to `GammaProfile` as the "All" / "Ex-0DTE" series. That matches the existing T11 contract
 * (`GET /gex/{underlying}/latest?filter=...`) with no new field needed; see the report for
 * why this was chosen over adding an `ex_zero_dte_total_gex` field to the point type.
 */
import { useState } from 'react';
import { UNDERLYINGS, type Underlying } from '../../api/types';
import { useGexResult } from '../../api/queries';
import { GammaProfile } from '../../components/GammaProfile';
import { KeyLevels } from '../../components/KeyLevels';

export function GammaProfileDemo() {
  const [symbol, setSymbol] = useState<Underlying>('SPX');

  const all = useGexResult(symbol, 'ALL', null);
  const exZeroDte = useGexResult(symbol, 'EX_ZERO_DTE', null);

  return (
    <div style={{ padding: 16 }}>
      <h1 style={{ fontSize: 18 }}>T14 demo — gamma profile &amp; key levels</h1>
      <div role="group" aria-label="Symbol" style={{ margin: '8px 0 16px' }}>
        {UNDERLYINGS.map((sym) => (
          <button key={sym} type="button" aria-pressed={sym === symbol} disabled={sym === symbol} onClick={() => setSymbol(sym)}>
            {sym}
          </button>
        ))}
      </div>

      {all.isLoading || exZeroDte.isLoading ? <p>Loading {symbol}…</p> : null}
      {all.isError || exZeroDte.isError ? <p role="alert">Failed to load {symbol} GEX.</p> : null}

      {all.data ? <KeyLevels levels={all.data.levels} snapshot={all.data.snapshot} /> : null}

      {all.data && exZeroDte.data ? (
        <GammaProfile
          allProfile={all.data.profile}
          exZeroDteProfile={exZeroDte.data.profile}
          spot={all.data.spot}
          flipPoint={all.data.levels.flip_point}
        />
      ) : null}
    </div>
  );
}
