/**
 * A z-score cell, coloured by standard deviations (T80).
 *
 * The board's colour scale is the one thing on the screen a trader reads without thinking, so it
 * has to encode magnitude honestly:
 *
 * - **Banded, not continuous.** |z| under 1 is unremarkable and gets no colour at all; a
 *   continuous gradient would make every row look like it was saying something.
 * - **Direction by hue, magnitude by intensity.** Sign matters — a yield rising and falling are
 *   opposite facts — and so does size, and the two are independent.
 * - **The number is always shown**, with an explicit sign. Colour is reinforcement and never the
 *   only carrier, so this survives a screenshot, a projector and colour blindness.
 */

export function zBand(z: number | null): string {
  if (z == null) return 'none';
  const magnitude = Math.abs(z);
  if (magnitude < 1) return 'flat';
  const direction = z > 0 ? 'up' : 'down';
  if (magnitude < 2) return `${direction}-1`;
  if (magnitude < 3) return `${direction}-2`;
  return `${direction}-3`;
}

export function ZCell({ z }: { z: number | null }) {
  const band = zBand(z);
  return (
    <td className={`is-numeric z-cell z-cell--${band}`}>
      {z == null ? '—' : `${z > 0 ? '+' : ''}${z.toFixed(2)}`}
    </td>
  );
}
