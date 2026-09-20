/**
 * Dropdown replacement for the old flat symbol switcher: two rows of up to 28 always-visible
 * buttons (`CORE_UNDERLYINGS` + `EXTENDED_UNDERLYINGS`) wrapping across the topbar. Same data,
 * same `onChange` contract (`useDashboardParams().setSymbol`) — only the presentation changes:
 * one trigger button showing the current symbol, opening a grouped listbox on click instead of
 * every symbol rendering as its own button all the time.
 */
import { useEffect, useId, useRef, useState } from 'react';
import { CORE_UNDERLYINGS, EXTENDED_UNDERLYINGS, type Underlying } from '../../api/types';

function AssetGroup({
  label,
  symbols,
  symbol,
  onSelect,
}: {
  label: string;
  symbols: readonly Underlying[];
  symbol: Underlying;
  onSelect: (s: Underlying) => void;
}) {
  return (
    <div className="asset-selector__group" role="group" aria-label={label}>
      <div className="asset-selector__group-label">{label}</div>
      {symbols.map((sym) => (
        <button
          key={sym}
          type="button"
          role="option"
          aria-selected={sym === symbol}
          className={
            sym === symbol ? 'asset-selector__option asset-selector__option--selected' : 'asset-selector__option'
          }
          onClick={() => onSelect(sym)}
        >
          {sym}
        </button>
      ))}
    </div>
  );
}

export function AssetSelector({ symbol, onChange }: { symbol: Underlying; onChange: (s: Underlying) => void }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const labelId = useId();
  const valueId = useId();

  // Closes on an outside click and on Escape, the same pair of dismissals every native
  // `<select>` gets for free -- a custom dropdown has to wire both up by hand.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  function select(next: Underlying) {
    setOpen(false);
    if (next !== symbol) onChange(next);
  }

  return (
    <div className="asset-selector" ref={rootRef}>
      <span id={labelId} className="topbar-field__label">
        Symbol
      </span>
      <button
        type="button"
        className="asset-selector__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        // Concatenates the visible "Symbol" caption with the current value ("SPX") into one
        // accessible name ("Symbol SPX") -- `aria-labelledby` replaces a button's own text
        // content for name computation rather than appending to it, so the value has to be
        // in the referenced list too, not just rendered as a child.
        aria-labelledby={`${labelId} ${valueId}`}
        onClick={() => setOpen((v) => !v)}
      >
        <span id={valueId} className="asset-selector__symbol">
          {symbol}
        </span>
        <span className="asset-selector__chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && (
        <div className="asset-selector__menu" role="listbox" aria-labelledby={labelId}>
          <AssetGroup label="Core" symbols={CORE_UNDERLYINGS} symbol={symbol} onSelect={select} />
          <AssetGroup label="Extended" symbols={EXTENDED_UNDERLYINGS} symbol={symbol} onSelect={select} />
        </div>
      )}
    </div>
  );
}
