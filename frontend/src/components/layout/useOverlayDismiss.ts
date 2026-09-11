/**
 * T63 — shared Escape/backdrop-click/focus-trap/focus-return behavior for the two modal-ish
 * overlays the workbench shell adds: `SideRail`'s narrow-width drawer and `CommandPalette`.
 * `AssetSelector` (components/layout/AssetSelector.tsx) already has a simpler version of
 * this for its own small popover — Escape-to-close, outside-click-to-close — but it never
 * needed a Tab focus trap or a "return focus to whatever opened it" step, because a listbox
 * popover isn't a full-screen modal. These two overlays are, so this hook adds both:
 *
 *  - On open, remembers `document.activeElement` (whatever had focus right before — for the
 *    drawer, that's its hamburger toggle button; for the palette, whatever the page focus
 *    was when Ctrl/Cmd+K fired) and moves focus to the first focusable element inside the
 *    overlay.
 *  - While open, Tab/Shift+Tab cycle only through the overlay's own focusable elements
 *    (a minimal focus trap — no `inert`/`aria-hidden` on the rest of the page, since both
 *    callers already render their backdrop as a full-viewport overlay above everything else).
 *  - Escape, or a pointer-down outside the returned container ref (i.e. on the backdrop),
 *    calls `onClose`.
 *  - On close, focus returns to whatever was remembered on open.
 */
import { useEffect, useRef, type RefObject } from 'react';

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useOverlayDismiss<T extends HTMLElement>(open: boolean, onClose: () => void): RefObject<T | null> {
  const containerRef = useRef<T>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (open) {
      previouslyFocused.current = document.activeElement as HTMLElement | null;
    } else if (previouslyFocused.current) {
      previouslyFocused.current.focus();
      previouslyFocused.current = null;
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;

    function focusables(): HTMLElement[] {
      const container = containerRef.current;
      if (!container) return [];
      return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
    }

    // Move focus into the overlay once it's mounted, unless something inside it already
    // grabbed focus itself (e.g. an `autoFocus` input).
    const container = containerRef.current;
    if (container && !container.contains(document.activeElement)) {
      focusables()[0]?.focus();
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusables();
      if (items.length === 0) return;
      const currentIndex = items.indexOf(document.activeElement as HTMLElement);
      if (event.shiftKey && currentIndex <= 0) {
        event.preventDefault();
        items[items.length - 1].focus();
      } else if (!event.shiftKey && currentIndex === items.length - 1) {
        event.preventDefault();
        items[0].focus();
      }
    }

    function onPointerDown(event: MouseEvent) {
      const current = containerRef.current;
      if (current && !current.contains(event.target as Node)) onClose();
    }

    // Capture phase so Escape closes the overlay even if a child handler (e.g. a text
    // input's own keydown) would otherwise swallow the event first.
    document.addEventListener('keydown', onKeyDown, true);
    document.addEventListener('mousedown', onPointerDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      document.removeEventListener('mousedown', onPointerDown);
    };
  }, [open, onClose]);

  return containerRef;
}
