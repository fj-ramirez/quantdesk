/**
 * An `i` button whose explanation appears on hover or focus.
 *
 * This exists to get *methodology* out of the reading path without deleting it. Several pages
 * opened with three or four lines of prose explaining how a number is computed before the
 * number itself was on screen; that text is worth keeping and worth reading once, but not
 * worth re-reading on every visit. What it does **not** do is hide a caveat that changes
 * whether a number should be trusted at all — the noise ceiling, the reused-split and
 * roll-gap claims, `reasons` on a regime row, the as-of note on a historical board. Those stay
 * on the page as sentences. The rule this file assumes: *if a reader who never opens the tip
 * would misread the number, it does not belong in a tip.*
 *
 * Three properties make this safe to use for the rest:
 *
 *  - **The text is always in the DOM and always in the accessibility tree.** Hidden means
 *    `opacity: 0`, never `display: none` or `visibility: hidden`, and the button carries
 *    `aria-describedby` — so a screen reader reads the explanation as part of the button, and
 *    Ctrl+F still finds it. Nothing here is information a sighted mouse user can reach and
 *    another reader cannot.
 *  - **Hover is not the only way in.** The panel opens on hover, on keyboard focus and on tap
 *    (a tap focuses the button), which is why this is a real `<button>` and not the `title`
 *    attribute it replaces in several tables — `title` is mouse-only, slow, and unreadable on
 *    a touch screen.
 *  - **Escape dismisses it** while focus stays put, per the WAI tooltip pattern.
 *
 * Hover and focus are handled in CSS (`:hover` / `:focus-within`), so the only state here is
 * the Escape dismissal.
 */
import { useId, useState, type ReactNode } from 'react';

export interface InfoTipProps {
  /** What this explains, for the button's accessible name: "About the noise ceiling". Not
   * rendered visually — the `i` glyph is the visual affordance. */
  label: string;
  /** Which edge the panel is anchored to. `"end"` for a tip near the right edge of a table or
   * a viewport, so the panel opens leftward instead of overflowing. */
  align?: 'start' | 'end';
  children: ReactNode;
}

export function InfoTip({ label, align = 'start', children }: InfoTipProps) {
  const id = useId();
  const [dismissed, setDismissed] = useState(false);

  return (
    <span
      className={`info-tip info-tip--${align}`}
      data-dismissed={dismissed ? 'true' : undefined}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setDismissed(true);
      }}
      // Re-arms on the way out, so a dismissed tip is available again next time rather than
      // staying dead for the rest of the session.
      onBlur={() => setDismissed(false)}
      onMouseLeave={() => setDismissed(false)}
    >
      <button type="button" className="info-tip__button" aria-label={`About ${label}`} aria-describedby={id}>
        <span aria-hidden="true">i</span>
      </button>
      <span id={id} role="note" className="info-tip__panel">
        {children}
      </span>
    </span>
  );
}
