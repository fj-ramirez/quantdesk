/**
 * T55 — the breakout ledger's per-event outcome (`continued` / `failed` / `pending`) and the
 * regime board's verdicts, rendered as one small chip component. Colour comes from
 * `theme/vizPalette.ts`'s `statusChipColor` map — never an inline hex — and, following the
 * same rule `Report.tsx`'s `LevelChip` already established (see that file's docstring for the
 * measured contrast numbers behind it), colour lives on the border and the dot only, never
 * the text: the chip's label always renders in `textPrimary`, so a reader who can't
 * distinguish the hue still reads the word.
 *
 * `status === null` (a symbol with zero events in the window, or a regime verdict that hasn't
 * loaded) renders the literal "n/a" chip in the neutral colour — an em dash would be
 * inconsistent with every other chip on the same table still being a labelled pill.
 */
import { useTheme } from '../../../../theme/ThemeContext';
import { statusChipColor, vizPaletteFor } from '../../../../theme/vizPalette';

export interface StatusChipProps {
  status: string | null;
  /** Overrides the displayed text; defaults to `status` verbatim (or "n/a"). */
  label?: string;
}

export function StatusChip({ status, label }: StatusChipProps) {
  const { theme } = useTheme();
  const palette = vizPaletteFor(theme);
  const effectiveStatus = status ?? 'n/a';
  const color = statusChipColor(effectiveStatus, palette);

  return (
    <span className="status-chip" style={{ borderColor: color }}>
      <span aria-hidden="true" className="status-chip__dot" style={{ background: color }} />
      <span className="status-chip__label" style={{ color: palette.textPrimary }}>
        {label ?? effectiveStatus}
      </span>
    </span>
  );
}
