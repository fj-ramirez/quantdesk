/**
 * T78 — the leaderboard page.
 *
 * Most of these tests are about one thing: **the noise ceiling must be on screen, and a row
 * that fails to clear it must say so in words.** That is not a styling preference. EdgeLab's
 * leaderboard is the output of a search over 134,377 combinations, and a search that hard
 * produces an impressive-looking best row whether or not any edge exists. A version of this
 * page that rendered the ranking and dropped the context would be actively misleading, and
 * would look completely fine.
 *
 * Runs against the MSW handlers in `../mocks/handlers.ts`, whose fixture deliberately mirrors
 * the real registry's current state: the top row is below its own ceiling.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { Leaderboard } from './Leaderboard';

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Leaderboard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the noise ceiling', () => {
  it('is on the page before any row can be read', async () => {
    renderPage();
    expect(await screen.findByText(/Noise ceiling:/)).toBeInTheDocument();
  });

  it('states the trial count that produced it', async () => {
    renderPage();
    // The denominator is the point: 5.6 is only meaningful next to "134,377 trials".
    expect(await screen.findByText(/134,377 trials searched/)).toBeInTheDocument();
  });

  it('says plainly when nothing clears it', async () => {
    renderPage();
    // Filter to futures, where the fixture's two rows are both below their ceiling.
    await screen.findByText(/Noise ceiling:/);
    fireEvent.change(screen.getByLabelText('Market'), { target: { value: 'futures' } });

    await waitFor(() =>
      expect(
        screen.getByText(/nothing here is distinguishable from luck/i),
      ).toBeInTheDocument(),
    );
  });
});

describe('rows below the ceiling', () => {
  it('are shown, not hidden', async () => {
    renderPage();
    // The 4.98 row does not clear its 5.5 ceiling and must still be in the table: that it is
    // the best of 134,377 attempts and still under the floor is the finding.
    expect(await screen.findByText('4.98')).toBeInTheDocument();
  });

  it('carry the verdict as text, not only as a colour', async () => {
    renderPage();
    await screen.findByText('4.98');
    // A screen reader and a greyscale screenshot both have to convey this.
    expect(screen.getAllByText(/below noise ceiling/i).length).toBeGreaterThan(0);
  });

  it('are marked per row, against that row’s own span', async () => {
    renderPage();
    await screen.findByText('4.98');

    // The long-span row clears its (much lower) ceiling; the short-span one does not, despite
    // having the higher Sharpe. Using one ceiling for every row would get this backwards.
    const above = screen.getByText('3.40').closest('tr');
    const below = screen.getByText('4.98').closest('tr');
    expect(above).toHaveClass('leaderboard-table__row--above');
    expect(below).toHaveClass('leaderboard-table__row--below');
    expect(within(below as HTMLElement).getByText(/below noise ceiling/i)).toBeInTheDocument();
  });
});

describe('caveats', () => {
  it('states the OOS-reuse caveat on the page, not only in the README', async () => {
    renderPage();
    expect(
      await screen.findByText(/out-of-sample split has been reused/i),
    ).toBeInTheDocument();
  });

  it('points at the paper watchlist as the only un-fitted evidence', async () => {
    renderPage();
    expect(
      await screen.findByText(/only genuinely out-of-sample evidence/i),
    ).toBeInTheDocument();
  });

  it('warns about futures roll gaps when futures rows are on screen', async () => {
    renderPage();
    expect(await screen.findByText(/roll gaps/i)).toBeInTheDocument();
  });
});

describe('filters and paging', () => {
  it('narrows the rows', async () => {
    renderPage();
    await screen.findByText('4.98');

    fireEvent.change(screen.getByLabelText('Market'), { target: { value: 'crypto' } });

    await waitFor(() => expect(screen.queryByText('4.98')).not.toBeInTheDocument());
    expect(screen.getByText('BTC/USDT')).toBeInTheDocument();
  });

  it('does not lower the ceiling when a filter is applied', async () => {
    renderPage();
    const before = (await screen.findByText(/134,377 trials searched/)).textContent;

    fireEvent.change(screen.getByLabelText('Market'), { target: { value: 'crypto' } });

    // Narrowing the view does not mean fewer experiments were run. If the denominator moved,
    // anyone could filter their way to a green row.
    await waitFor(() => expect(screen.getByText('BTC/USDT')).toBeInTheDocument());
    expect(screen.getByText(/134,377 trials searched/).textContent).toBe(before);
  });

  it('reports the true match count, not the page size', async () => {
    renderPage();
    expect(await screen.findByText(/of 4$/)).toBeInTheDocument();
  });

  it('offers the filter options the API reported', async () => {
    renderPage();
    // The select renders immediately with only "All"; its options arrive with `/status`, so
    // this has to wait for the option rather than for the control.
    expect(await screen.findByRole('option', { name: 'futures' })).toBeInTheDocument();
    const markets = screen.getByLabelText('Market');
    // Options come from `/status`, so a strategy family added later needs no frontend change.
    expect(within(markets).getByRole('option', { name: 'crypto' })).toBeInTheDocument();
  });
});

describe('the status strip', () => {
  it('leads with the trial count and says why it matters', async () => {
    renderPage();
    expect(await screen.findByText('Trials searched')).toBeInTheDocument();
    expect(screen.getByText(/losers included/i)).toBeInTheDocument();
  });
});

describe('trial detail', () => {
  it('opens on a row and shows both sides of the split', async () => {
    renderPage();
    const row = (await screen.findByText('4.98')).closest('tr');

    fireEvent.click(row as HTMLElement);

    const drawer = await screen.findByRole('dialog', { name: 'Trial detail' });
    // The drawer opens on "Loading…" and fills in when `/trials/{hash}` resolves.
    expect(await within(drawer).findByText('In-sample')).toBeInTheDocument();
    expect(within(drawer).getByText('Out-of-sample')).toBeInTheDocument();
    // The gap between the two columns is the point of showing them together.
    expect(within(drawer).getByText(/tuned to its own history/i)).toBeInTheDocument();
  });
});
