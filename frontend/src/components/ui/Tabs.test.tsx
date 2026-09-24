import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useSearchParams } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { Tabs } from './Tabs';
import { useTabParam } from './useTabParam';

const VALUES = ['ranked', 'notrade', 'record'] as const;

function Harness() {
  const [tab, setTab] = useTabParam('tab', VALUES);
  const [params] = useSearchParams();
  return (
    <>
      <Tabs
        label="Sections"
        active={tab}
        onChange={setTab}
        tabs={[
          { value: 'ranked', label: 'Ranked', count: 25 },
          { value: 'notrade', label: 'No trade', count: 0 },
          { value: 'record', label: 'Track record', count: null },
        ]}
      >
        <p>panel: {tab}</p>
      </Tabs>
      <output data-testid="search">{params.toString()}</output>
    </>
  );
}

function renderAt(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Harness />
    </MemoryRouter>,
  );
}

describe('Tabs + useTabParam', () => {
  it('defaults to the first tab and keeps the default out of the URL', () => {
    renderAt();
    expect(screen.getByRole('tab', { name: /Ranked/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('panel: ranked');
    expect(screen.getByTestId('search')).toHaveTextContent(/^$/);
  });

  it('renders a zero count as 0 and a null count as nothing', () => {
    renderAt();
    expect(screen.getByRole('tab', { name: /No trade/ })).toHaveTextContent('No trade0');
    expect(screen.getByRole('tab', { name: 'Track record' })).toHaveTextContent(/^Track record$/);
  });

  it('writes the chosen tab to the URL and restores it from a deep link', () => {
    renderAt();
    fireEvent.click(screen.getByRole('tab', { name: 'Track record' }));
    expect(screen.getByTestId('search')).toHaveTextContent('tab=record');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('panel: record');
  });

  it('falls back to the first tab on an unknown value', () => {
    renderAt('/?tab=bogus');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('panel: ranked');
  });

  it('moves with the arrow keys, wrapping, and keeps one tab stop', () => {
    renderAt('/?tab=record');
    const record = screen.getByRole('tab', { name: 'Track record' });
    expect(record).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('tab', { name: /Ranked/ })).toHaveAttribute('tabindex', '-1');
    fireEvent.keyDown(record, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: /Ranked/ })).toHaveFocus();
    expect(screen.getByRole('tabpanel')).toHaveTextContent('panel: ranked');
    fireEvent.keyDown(screen.getByRole('tab', { name: /Ranked/ }), { key: 'End' });
    expect(record).toHaveFocus();
  });
});
