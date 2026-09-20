/**
 * The launcher's background texture: a candlestick field behind the hero and a set of
 * flowing curves along the bottom edge.
 *
 * Both are *texture*, not data. They render at single-digit opacity, carry `aria-hidden`, and
 * are never labelled with a symbol, an axis or a price — a market-analysis desk must not put
 * invented quotes on screen in a way that could be read as real ones. The series below comes
 * from a fixed seed rather than `Math.random()` so the page looks the same on every render
 * (and in every screenshot diff) instead of shimmering on each mount.
 */

interface Candle {
  x: number;
  open: number;
  close: number;
  high: number;
  low: number;
}

const FIELD_W = 900;
const FIELD_H = 340;
const CANDLE_COUNT = 58;
const CANDLE_PITCH = FIELD_W / CANDLE_COUNT;

/** A 32-bit LCG — enough randomness to look like a tape, entirely reproducible. */
function makeCandles(seed: number): Candle[] {
  let state = seed;
  const next = () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };

  const candles: Candle[] = [];
  let level = FIELD_H * 0.62;
  for (let i = 0; i < CANDLE_COUNT; i += 1) {
    // A gentle upward drift plus noise, clamped well inside the field so no candle clips.
    const drift = -1.1;
    const step = drift + (next() - 0.5) * 26;
    const open = level;
    const close = Math.min(FIELD_H * 0.92, Math.max(FIELD_H * 0.08, level + step));
    const body = Math.abs(close - open);
    const high = Math.min(open, close) - (2 + next() * (6 + body));
    const low = Math.max(open, close) + (2 + next() * (6 + body));
    candles.push({ x: i * CANDLE_PITCH + CANDLE_PITCH / 2, open, close, high, low });
    level = close;
  }
  return candles;
}

const CANDLES = makeCandles(20260919);

export function LauncherBackdrop() {
  return (
    <div className="lx-backdrop" aria-hidden="true">
      <svg
        className="lx-backdrop__tape"
        viewBox={`0 0 ${FIELD_W} ${FIELD_H}`}
        preserveAspectRatio="xMaxYMid slice"
        focusable="false"
      >
        {CANDLES.map((candle) => {
          const up = candle.close <= candle.open;
          const top = Math.min(candle.open, candle.close);
          const height = Math.max(1.5, Math.abs(candle.close - candle.open));
          return (
            <g key={candle.x} className={up ? 'lx-candle lx-candle--up' : 'lx-candle lx-candle--down'}>
              <line x1={candle.x} y1={candle.high} x2={candle.x} y2={candle.low} strokeWidth={1} />
              <rect x={candle.x - CANDLE_PITCH * 0.32} y={top} width={CANDLE_PITCH * 0.64} height={height} />
            </g>
          );
        })}
      </svg>

      <svg className="lx-backdrop__flow" viewBox="0 0 1200 260" preserveAspectRatio="none" focusable="false">
        <path d="M-40 210 C 220 120, 380 250, 620 170 S 1000 60, 1240 130" />
        <path d="M-40 244 C 200 170, 420 268, 660 196 S 1020 112, 1240 172" />
        <path d="M-40 176 C 260 96, 400 214, 640 136 S 1040 24, 1240 92" />
      </svg>
    </div>
  );
}
