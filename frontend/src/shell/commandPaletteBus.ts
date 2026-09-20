/**
 * How a button opens `CommandPalette` without owning its state.
 *
 * The palette keeps its own `open` state and has always been driven by a global Ctrl/Cmd+K
 * listener, so a trigger elsewhere in the tree (the launcher's search pill) has nothing to
 * lift: this is the same window-level channel, given a name. Lives in its own module rather
 * than in `CommandPalette.tsx` so that file keeps exporting only a component
 * (`react-refresh/only-export-components` — the project's fixed warning count must not grow,
 * see `regimeRows.ts`).
 */
export const COMMAND_PALETTE_OPEN_EVENT = 'quantdesk:open-command-palette';

export function openCommandPalette(): void {
  window.dispatchEvent(new Event(COMMAND_PALETTE_OPEN_EVENT));
}
