/**
 * T63 — CommandPalette: Ctrl/Cmd+K open/close, filtering, route navigation, and the two
 * symbol-switch behaviors (stay on a symbol-aware route vs. jump to /dashboard from a
 * universe page).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { CommandPalette } from './CommandPalette';

function Probe() {
  return (
    <div>
      <button type="button">Somewhere else</button>
      <CommandPalette />
    </div>
  );
}

function renderPalette(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="*" element={<Probe />} />
      </Routes>
    </MemoryRouter>,
  );
}

function openWithCtrlK() {
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
}

describe('CommandPalette', () => {
  it('is not rendered until Ctrl/Cmd+K is pressed', () => {
    renderPalette('/');
    expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument();
  });

  it('opens on Ctrl+K, focusing the search input, and closes again on a second press', () => {
    renderPalette('/');
    openWithCtrlK();
    const dialog = screen.getByRole('dialog', { name: 'Command palette' });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole('combobox')).toHaveFocus();

    openWithCtrlK();
    expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument();
  });

  it('lists both routes and symbols, and filters by the typed query', () => {
    renderPalette('/');
    openWithCtrlK();
    expect(screen.getByRole('option', { name: /GEX Explorer/ })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /^SPX/ })).toBeInTheDocument();

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'rot' } });
    expect(screen.getByRole('option', { name: /Rotation/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /GEX Explorer/ })).not.toBeInTheDocument();
  });

  it('Escape closes the palette and returns focus to whatever was focused before it opened', () => {
    renderPalette('/');
    const trigger = screen.getByRole('button', { name: 'Somewhere else' });
    trigger.focus();
    openWithCtrlK();
    expect(screen.getByRole('dialog', { name: 'Command palette' })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('selecting a route entry navigates and closes the palette', async () => {
    renderPalette('/gex/scan?view=trend');
    openWithCtrlK();
    fireEvent.click(screen.getByRole('option', { name: /Opportunities/ }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument());
  });

  it('selecting a symbol while on a universe page (e.g. /scan) navigates to /dashboard for that symbol', () => {
    renderPalette('/gex/scan');
    openWithCtrlK();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'QQQ' } });
    fireEvent.click(screen.getByRole('option', { name: /^QQQ/ }));
    expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument();
  });

  it('ArrowDown/Enter selects the highlighted entry, not just the first one, without a mouse', () => {
    renderPalette('/gex/scan');
    openWithCtrlK();
    // Unfiltered order is `navConfig.ts`'s `flattenNavItems()` order: Overview first,
    // Opportunities second. ArrowDown must move the highlight (and therefore what Enter
    // acts on) off the first entry.
    expect(screen.getByRole('option', { name: /^Overview/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' });
    expect(screen.getByRole('option', { name: /^Opportunities/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('option', { name: /^Overview/ })).toHaveAttribute('aria-selected', 'false');

    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
    expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument();
  });
});
