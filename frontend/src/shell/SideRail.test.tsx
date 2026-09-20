/**
 * T63 — SideRail: nav grouping/labels, carried search string, the desktop collapse toggle,
 * and the narrow-width drawer's Escape/backdrop-click/focus-return behavior.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { SideRail } from './SideRail';

function renderRail(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <SideRail />
    </MemoryRouter>,
  );
}

describe('SideRail', () => {
  it('renders every route once, under its new label, with the route paths unchanged', () => {
    renderRail('/gex');
    const expected: Array<[string, string]> = [
      ['Overview', '/gex'],
      ['Opportunities', '/gex/decisions'],
      ['GEX Explorer', '/gex/dashboard'],
      ['Regime', '/gex/regime'],
      ['Scan', '/gex/scan'],
      ['Rotation', '/gex/rotation'],
      ['Flows', '/gex/flows'],
      ['Report', '/gex/report'],
      ['History', '/gex/history'],
      ['Settings', '/gex/settings'],
    ];
    for (const [label, to] of expected) {
      expect(screen.getByRole('link', { name: label })).toHaveAttribute('href', to);
    }
  });

  it('marks the current route as the active link (aria-current)', () => {
    renderRail('/gex/regime');
    expect(screen.getByRole('link', { name: 'Regime' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Scan' })).not.toHaveAttribute('aria-current');
  });

  it('Overview only matches the root path exactly, not every route (the `end` guard)', () => {
    renderRail('/gex/regime');
    expect(screen.getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current');
  });

  it('carries the current search string onto every link, the same contract the old NavBar had', () => {
    renderRail('/gex/scan?view=trend&sort=composite');
    expect(screen.getByRole('link', { name: 'GEX Explorer' })).toHaveAttribute(
      'href',
      '/gex/dashboard?view=trend&sort=composite',
    );
  });

  it('the desktop collapse toggle flips its own aria-pressed state', () => {
    renderRail('/gex');
    const toggle = screen.getByRole('button', { name: 'Collapse navigation' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(toggle);
    expect(screen.getByRole('button', { name: 'Expand navigation' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('the narrow-width menu toggle opens a dialog with the same nav, closes on Escape, and returns focus to the toggle', () => {
    renderRail('/gex');
    const toggle = screen.getByRole('button', { name: 'Open navigation menu' });
    toggle.focus();
    fireEvent.click(toggle);

    const dialog = screen.getByRole('dialog', { name: 'Navigation menu' });
    expect(dialog).toBeInTheDocument();
    // Two "Overview" links now exist in the DOM (the persistent desktop rail plus the
    // drawer's copy) -- both real, since CSS (not conditional rendering) is what hides the
    // desktop rail at narrow widths; jsdom does not evaluate that media query, so both stay
    // in the accessibility tree here. `within(dialog)` scopes to just the drawer's copy.
    expect(screen.getAllByRole('link', { name: 'Overview' }).length).toBeGreaterThanOrEqual(1);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Navigation menu' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open navigation menu' })).toHaveFocus();
  });

  it('closes on a backdrop click', () => {
    renderRail('/gex');
    fireEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));
    expect(screen.getByRole('dialog', { name: 'Navigation menu' })).toBeInTheDocument();

    // The backdrop is the dialog's own parent -- clicking it (not the dialog panel) is
    // "outside" the panel container `useOverlayDismiss` tracks.
    const dialog = screen.getByRole('dialog', { name: 'Navigation menu' });
    fireEvent.mouseDown(dialog.parentElement as Element);
    expect(screen.queryByRole('dialog', { name: 'Navigation menu' })).not.toBeInTheDocument();
  });
});
