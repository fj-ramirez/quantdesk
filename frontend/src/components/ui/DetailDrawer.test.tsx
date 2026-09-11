/**
 * T64 — DetailDrawer: focus moves in on open, Tab traps inside the panel, Escape/backdrop
 * close it, and focus returns to whatever triggered it. Modeled on the same pattern
 * `CommandPalette.test.tsx`/`SideRail.test.tsx` already use for `useOverlayDismiss` callers:
 * the drawer must stay mounted across open/close (see this file's own docstring), so the test
 * harness below toggles `open` via state rather than conditionally rendering the component.
 */
import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DetailDrawer } from './DetailDrawer';

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        Open SPX row
      </button>
      <DetailDrawer open={open} onClose={() => setOpen(false)} title="SPX opportunity detail">
        <button type="button">Inside action</button>
      </DetailDrawer>
    </div>
  );
}

function openDrawer() {
  const trigger = screen.getByRole('button', { name: 'Open SPX row' });
  trigger.focus();
  fireEvent.click(trigger);
  return trigger;
}

describe('DetailDrawer', () => {
  it('is not rendered until open', () => {
    render(<Harness />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('opens as a labelled dialog and moves focus inside it', () => {
    render(<Harness />);
    openDrawer();
    const dialog = screen.getByRole('dialog', { name: 'SPX opportunity detail' });
    expect(dialog).toBeInTheDocument();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  });

  it('traps Tab within the panel: Shift+Tab from the first control wraps to the last', () => {
    render(<Harness />);
    openDrawer();
    const closeButton = screen.getByRole('button', { name: 'Close' });
    const insideAction = screen.getByRole('button', { name: 'Inside action' });
    expect(closeButton).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(insideAction).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Tab' });
    expect(closeButton).toHaveFocus();
  });

  it('Escape closes the drawer and returns focus to the trigger that opened it', () => {
    render(<Harness />);
    const trigger = openDrawer();
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('a backdrop click closes the drawer and returns focus to the trigger', () => {
    render(<Harness />);
    const trigger = openDrawer();
    const dialog = screen.getByRole('dialog');

    fireEvent.mouseDown(dialog.parentElement as Element);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('the Close button closes the drawer and returns focus to the trigger', () => {
    render(<Harness />);
    const trigger = openDrawer();

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
