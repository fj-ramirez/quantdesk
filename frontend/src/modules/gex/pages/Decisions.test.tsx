/**
 * T60 -- `/decisions`, rendered end to end through the real query hooks against the MSW
 * handlers, the same shape as `Regime.test.tsx`. `decisions.json` is the live backend response
 * recorded in-process on 2026-09-10 (see `mocks/fixtures/scan/README.md`'s "Decisions
 * fixture" section) -- every number asserted below is one the engine actually produced.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { server } from '../mocks/server';
import { ThemeProvider } from '../../../theme/ThemeContext';
import { Decisions } from './Decisions';
import decisionsFixture from '../mocks/fixtures/scan/decisions.json';
import type { DecisionsResponse } from '../api/types';

const fixture = decisionsFixture as DecisionsResponse;

function renderDecisions(initialPath = '/decisions') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/decisions" element={<Decisions />} />
            <Route path="/dashboard" element={<p>dashboard stand-in</p>} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

async function awaitLoaded() {
  await waitFor(() => expect(screen.queryByText(/Scoring opportunities/)).not.toBeInTheDocument());
}

describe('Decisions page', () => {
  it('renders one row per ranked opportunity, in API order, plus the disclaimer', async () => {
    renderDecisions();
    await awaitLoaded();
    const table = await screen.findByRole('table', { name: /ranked opportunities/i });
    const bodyRows = within(table).getAllByRole('row').slice(1);
    expect(bodyRows).toHaveLength(fixture.ranked.length);
    // API order: the first body row is the first ranked opportunity.
    expect(bodyRows[0]).toHaveTextContent(fixture.ranked[0].underlying);
    expect(bodyRows[0]).toHaveTextContent(fixture.ranked[0].grade);
    expect(screen.getByText(fixture.generated_from)).toBeInTheDocument();
  });

  it('lists every no-trade symbol with its first reason verbatim', async () => {
    renderDecisions();
    await awaitLoaded();
    const quiet = fixture.symbols.filter((s) => s.opportunities.length === 0);
    expect(quiet.length).toBeGreaterThan(0);
    const region = screen.getByRole('region', { name: 'No trade' });
    for (const s of quiet) {
      // `getAllByText`: two stale sector ETFs can share the identical reason sentence.
      expect(within(region).getAllByText(s.no_trade_reasons[0]).length).toBeGreaterThan(0);
      expect(within(region).getByRole('link', { name: s.underlying })).toBeInTheDocument();
    }
  });

  it('clicking a row opens the focus-managed detail drawer with the thesis, invalidation and levels', async () => {
    renderDecisions();
    await awaitLoaded();
    const first = fixture.ranked[0];
    const table = await screen.findByRole('table', { name: /ranked opportunities/i });
    const firstRow = within(table).getAllByRole('row')[1];
    // `.focus()` first, matching `SideRail.test.tsx`'s pattern for a `useOverlayDismiss`
    // caller -- jsdom's `fireEvent.click` does not itself move focus the way a real browser's
    // default click action does, so the row must already have focus for the drawer's
    // focus-return-to-trigger behavior to be observable below.
    firstRow.focus();
    fireEvent.click(firstRow);

    const panel = await screen.findByRole('dialog', { name: new RegExp(`^${first.underlying} `) });
    // Focus moved into the drawer (the first focusable element -- its own Close button).
    expect(panel).toContainElement(document.activeElement as HTMLElement);
    for (const line of first.thesis) expect(within(panel).getByText(line)).toBeInTheDocument();
    for (const line of first.invalidation) expect(within(panel).getByText(line)).toBeInTheDocument();
    expect(within(panel).getByText(first.structure)).toBeInTheDocument();
    expect(within(panel).getByText(first.entry_label)).toBeInTheDocument();
    // The score breakdown sums to the score and its maxima to 100.
    expect(first.score_breakdown.reduce((a, c) => a + c.points, 0)).toBe(first.score);
    expect(first.score_breakdown.reduce((a, c) => a + c.max_points, 0)).toBe(100);
    expect(within(panel).getByText(`${first.score}/100`)).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    // Focus returns to the row that opened it.
    expect(firstRow).toHaveFocus();
  });

  it('opens and closes the detail drawer entirely by keyboard -- Tab to the row, Enter to open, Escape to close, focus returns to the row', async () => {
    renderDecisions();
    await awaitLoaded();
    const first = fixture.ranked[0];
    const table = await screen.findByRole('table', { name: /ranked opportunities/i });
    const firstRow = within(table).getAllByRole('row')[1];

    // Tab reaches the row: it is a real Tab stop (`tabIndex={0}` from `ScanTable`), so
    // moving focus onto it directly is the same outcome a keyboard user's Tab traversal
    // would produce -- jsdom has no layout/tab-order engine to drive an actual Tab keypress
    // across a whole page, so this asserts the row is reachable and focusable, then drives
    // the rest of the interaction with real key events.
    firstRow.focus();
    expect(firstRow).toHaveFocus();

    // Enter opens the drawer (not a click) -- `ScanTable`'s own onKeyDown handler.
    fireEvent.keyDown(firstRow, { key: 'Enter' });
    const panel = await screen.findByRole('dialog', { name: new RegExp(`^${first.underlying} `) });
    expect(panel).toContainElement(document.activeElement as HTMLElement);

    // Escape closes it (not the Close button) -- `useOverlayDismiss`'s document-level handler.
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    // Focus returns to the row that opened it.
    expect(firstRow).toHaveFocus();
  });

  it('Space also activates a focused row, matching Enter', async () => {
    renderDecisions();
    await awaitLoaded();
    const first = fixture.ranked[0];
    const table = await screen.findByRole('table', { name: /ranked opportunities/i });
    const firstRow = within(table).getAllByRole('row')[1];
    firstRow.focus();
    fireEvent.keyDown(firstRow, { key: ' ' });
    expect(await screen.findByRole('dialog', { name: new RegExp(`^${first.underlying} `) })).toBeInTheDocument();
  });

  it('reads status summary before filter/threshold before the ranked table, per the plan\'s Opportunities hierarchy', async () => {
    const { container } = renderDecisions();
    await awaitLoaded();
    // `compareDocumentPosition`: DOCUMENT_POSITION_FOLLOWING (4) means the first node comes
    // before the second in document order -- i.e. reading/tab order, not visual position.
    const summary = screen.getByRole('group', { name: 'Opportunities at a glance' });
    const toolbar = screen.getByRole('group', { name: 'Filter' });
    const table = screen.getByRole('table', { name: /ranked opportunities/i });
    expect(summary.compareDocumentPosition(toolbar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(toolbar.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container).toBeInTheDocument();
  });

  it('?min_score=75 drives the toolbar and trims the ranking to A grades', async () => {
    renderDecisions('/decisions?min_score=75');
    await awaitLoaded();
    expect(within(screen.getByRole('group', { name: 'Min grade' })).getByRole('button', { name: 'A' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    const expected = fixture.ranked.filter((o) => o.score >= 75).length;
    const table = await screen.findByRole('table', { name: /ranked opportunities/i });
    expect(within(table).getAllByRole('row').slice(1)).toHaveLength(expected);
  });

  it('renders an error state on a transport failure, never a raw response body', async () => {
    server.use(http.get('*/api/gex/decisions', () => HttpResponse.error()));
    renderDecisions();
    await awaitLoaded();
    expect(await screen.findByText(/Could not load the decision engine/)).toBeInTheDocument();
    expect(screen.queryByText(/detail/)).not.toBeInTheDocument();
  });

  it('renders an empty state when nothing is ranked, never a blank page', async () => {
    server.use(
      http.get('*/api/gex/decisions', () =>
        HttpResponse.json({ ...fixture, ranked: [], symbols: [], no_chain: ['SPX'] }),
      ),
    );
    renderDecisions();
    await awaitLoaded();
    expect(await screen.findByRole('region', { name: 'No opportunities at this threshold' })).toBeInTheDocument();
    expect(screen.getByText(/No chain captured yet: SPX/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------------------
// T61: the track record block.
// ---------------------------------------------------------------------------------------

import decisionsHistoryFixture from '../mocks/fixtures/scan/decisions_history.json';
import type { DecisionsHistoryResponse } from '../api/types';

const historyFixture = decisionsHistoryFixture as DecisionsHistoryResponse;

/** A hand-built variant of the live (all-pending) recording with three rows resolved, so the
 * summary's rate/R columns are exercised. Labelled synthetic: the live day-one recording has
 * nothing resolved, and the numbers here are chosen, not measured. */
function resolvedVariant(): DecisionsHistoryResponse {
  const records = historyFixture.records.map((r, i) => {
    if (i === 0) return { ...r, outcome: 'target' as const, fill: r.entry, result_r: 2.5, outcome_note: 'filled at the wall; target hit' };
    if (i === 1) return { ...r, outcome: 'stop' as const, fill: r.entry, result_r: -1, outcome_note: 'filled at the wall; stop hit' };
    if (i === 2) return { ...r, outcome: 'pending' as const, fill: r.entry, mark_r: 0.4, outcome_note: 'open, 2 of 10 bars held' };
    return r;
  });
  const overall = {
    ...historyFixture.summary.overall,
    pending: historyFixture.summary.overall.n - 2,
    resolved: 2,
    targets: 1,
    stops: 1,
    hit_rate: null,
    win_rate: null,
    avg_r: 0.75,
    total_r: 1.5,
    best_r: 2.5,
    worst_r: -1,
  };
  return { ...historyFixture, records, summary: { ...historyFixture.summary, overall } };
}

describe('Decisions page -- track record', () => {
  it('renders the live all-pending recording with withheld rates and the scoring note', async () => {
    renderDecisions();
    await awaitLoaded();
    const region = await screen.findByRole('region', { name: 'Track record' });
    expect(within(region).getByText(historyFixture.note)).toBeInTheDocument();
    const summary = within(region).getByRole('table', { name: 'Track record summary' });
    const all = within(summary).getByRole('row', { name: /^All/ });
    expect(all).toHaveTextContent(String(historyFixture.summary.overall.n));
    expect(within(all).getAllByText('n<5')).toHaveLength(2); // hit and win withheld
    const ledger = within(region).getByRole('table', { name: 'Recorded opportunities' });
    expect(within(ledger).getAllByRole('row')).toHaveLength(historyFixture.records.length + 1);
    expect(within(ledger).getAllByText('pending')).toHaveLength(historyFixture.records.length);
  });

  it('renders resolved rows with signed R and pending marks as unrealized', async () => {
    server.use(http.get('*/api/gex/decisions/history', () => HttpResponse.json(resolvedVariant())));
    renderDecisions();
    await awaitLoaded();
    const region = await screen.findByRole('region', { name: 'Track record' });
    expect(within(region).getByText('+2.50R')).toBeInTheDocument();
    expect(within(region).getByText('-1.00R')).toBeInTheDocument();
    expect(within(region).getByText('unrealized')).toBeInTheDocument();
    const summary = within(region).getByRole('table', { name: 'Track record summary' });
    expect(within(summary).getByRole('row', { name: /^All/ })).toHaveTextContent('+1.50R');
  });

  it('Record now posts, reports the counts, and never a raw body on failure', async () => {
    renderDecisions();
    await awaitLoaded();
    const region = await screen.findByRole('region', { name: 'Track record' });
    fireEvent.click(within(region).getByRole('button', { name: 'Record now' }));
    expect(await within(region).findByRole('status')).toHaveTextContent('recorded 0, scored 25, resolved 0');

    server.use(http.post('*/api/gex/decisions/record', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })));
    fireEvent.click(within(region).getByRole('button', { name: 'Record now' }));
    expect(await within(region).findByRole('alert')).toHaveTextContent('The record run failed.');
    expect(screen.queryByText(/boom/)).not.toBeInTheDocument();
  });

  it('renders the empty track record when nothing is stored', async () => {
    server.use(
      http.get('*/api/gex/decisions/history', () =>
        HttpResponse.json({ ...historyFixture, records: [], summary: { ...historyFixture.summary, overall: { ...historyFixture.summary.overall, n: 0 } } }),
      ),
    );
    renderDecisions();
    await awaitLoaded();
    expect(await screen.findByRole('region', { name: 'Nothing recorded yet' })).toBeInTheDocument();
  });
});
