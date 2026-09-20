/**
 * T78 — the paper-candidates page.
 *
 * The thing worth testing here is the **ordering**, and it is worth testing because it is a
 * claim about honesty rather than a layout preference. This table is a forward record: each row
 * was committed to at a timestamp, and everything since was never fitted. Sorting it by
 * performance would convert the only un-mined evidence in the module into a second leaderboard,
 * and it would look like an improvement while doing it.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { Paper } from './Paper';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Paper />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it('orders candidates by promotion date, oldest first', async () => {
  renderPage();
  await screen.findByText('ETH/USDT');

  const rows = screen.getAllByRole('row').slice(1); // drop the header row
  // The fixture's first candidate has the *worse* promoted Sharpe (1.42 vs 1.05 is higher, but
  // it was promoted earlier) -- so a performance sort would reorder these and this would fail.
  expect(within(rows[0]).getByText('2026-07-04')).toBeInTheDocument();
  expect(within(rows[1]).getByText('2026-08-12')).toBeInTheDocument();
});

it('says what the promotion gates were', async () => {
  renderPage();
  // A reader has to know a candidate is not simply "the best row" -- it cleared doubled costs,
  // a neighbourhood check, walk-forward consistency and a correlation cap.
  expect(await screen.findByText(/doubled/i)).toBeInTheDocument();
  expect(screen.getByText(/walk-forward consistency gate/i)).toBeInTheDocument();
});

it('frames forward performance as the only un-fitted evidence', async () => {
  renderPage();
  expect(await screen.findByText(/never fitted/i)).toBeInTheDocument();
});

it('shows each gate’s value per row', async () => {
  renderPage();
  await screen.findByText('ETH/USDT');

  const row = screen.getByText('ETH/USDT').closest('tr') as HTMLElement;
  expect(within(row).getByText('0.81')).toBeInTheDocument(); // sharpe at 2x costs
  expect(within(row).getByText('5/6')).toBeInTheDocument(); // walk-forward windows
  expect(within(row).getByText('0.31')).toBeInTheDocument(); // max correlation
});

describe('column headings', () => {
  it('names the doubled-cost column in plain terms', async () => {
    renderPage();
    expect(await screen.findByText('At 2× costs')).toBeInTheDocument();
  });
});
