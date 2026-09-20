/**
 * T80 — the terminal.
 *
 * These tests are about one claim: **the as-of control governs the whole module, and the screen
 * never lets you mistake a historical view for a live one.** That is the difference between this
 * and a quote board, and it is the kind of thing that breaks silently — a control that stops
 * being threaded through still renders, still accepts input, and just quietly shows you today.
 *
 * The MSW fixtures deliberately vary their answers by `as_of` (fewer scored rows early, no edges
 * before the night they were estimated), so a control that stopped working would change the
 * assertions below rather than pass them.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { TerminalFrame } from './TerminalFrame';
import { Board } from './pages/Board';
import { Brief } from './pages/Brief';
import { Graph } from './pages/Graph';
import { Policy } from './pages/Policy';
import { zBand } from './components/ZCell';

function renderTerminal(path = '/terminal') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/terminal" element={<TerminalFrame />}>
            <Route index element={<Board />} />
            <Route path="graph" element={<Graph />} />
            <Route path="policy" element={<Policy />} />
            <Route path="brief" element={<Brief />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the as-of control', () => {
  it('says "live" when no moment is pinned', async () => {
    renderTerminal();
    expect(await screen.findByText(/Live — latest known/)).toBeInTheDocument();
  });

  it('is read from the URL, so a view of the world is a shareable link', async () => {
    renderTerminal('/terminal?as_of=2020-03-16T17:00:00.000Z');
    expect(await screen.findByText(/Historical/)).toBeInTheDocument();
    // The control itself reflects it, not just the badge. `datetime-local` renders in local
    // time, so assert on the date part rather than the exact instant.
    expect((screen.getByLabelText('As of') as HTMLInputElement).value).toContain('2020-03-16');
  });

  it('warns unmistakably when pinned to the past', async () => {
    renderTerminal('/terminal?as_of=2020-03-16T17:00:00.000Z');
    // Not a subtle input state: the expensive mistake is reading a historical board as live.
    expect(
      await screen.findByText(/showing only what was knowable then/i),
    ).toBeInTheDocument();
  });

  it('actually changes what the board shows', async () => {
    renderTerminal('/terminal?as_of=2020-03-16T17:00:00.000Z');
    // The fixture has fewer scored series at an early as-of, mirroring the real store.
    await waitFor(() => expect(screen.getByText(/of \d+ series scored/)).toBeInTheDocument());
    expect(screen.getByText(/2 of \d+ series scored at this as-of/)).toBeInTheDocument();
  });

  it('can be cleared back to live', async () => {
    renderTerminal('/terminal?as_of=2020-03-16T17:00:00.000Z');
    fireEvent.click(await screen.findByRole('button', { name: /Back to live/ }));
    expect(await screen.findByText(/Live — latest known/)).toBeInTheDocument();
  });
});

describe('the regime strip in the frame', () => {
  it('shows the state on every screen, with its evidence', async () => {
    renderTerminal();
    expect(await screen.findByText('Quiet')).toBeInTheDocument();
    // A label with no z-scores behind it is an opinion you cannot check.
    expect(screen.getByText('ust.10y.real')).toBeInTheDocument();
    expect(screen.getByText('0.71')).toBeInTheDocument();
  });
});

describe('the change board', () => {
  it('ranks by how unusual the move is, not by its size', async () => {
    renderTerminal();
    await screen.findByText('ust_cc.3m');
    const rows = screen.getAllByRole('row');
    // ust_cc.3m moved 7bp with z 3.22; ust.5y.nominal moved twice as much with a lower z.
    // The board is a z-board, so the smaller move ranks first.
    expect(within(rows[1]).getByText('ust_cc.3m')).toBeInTheDocument();
  });

  it('shows the window a z was measured against', async () => {
    renderTerminal();
    await screen.findByText('ust_cc.3m');
    // A z of 2 from 60 observations and from 250 are different claims.
    expect(screen.getAllByText('250').length).toBeGreaterThan(0);
  });

  it('shows volatility context beside the z', async () => {
    renderTerminal();
    // A 2-sigma move against a compressed window is a different statement.
    expect((await screen.findAllByText('compressed')).length).toBeGreaterThan(0);
  });

  it('never renders a missing value as a zero', async () => {
    renderTerminal();
    await screen.findByText('ust_cc.3m');

    // The unscored series are listed with a reason, not folded into the table as 0.00.
    // `getAllByText`: the same series is also one of the regime strip's three legs, which is
    // itself the point -- it has a z there and no board row here, and both are true.
    expect(screen.getAllByText('credit.hy.oas').length).toBeGreaterThan(0);
    expect(screen.getByText(/no data at this as-of/)).toBeInTheDocument();
    expect(screen.getByText(/not enough history to score/)).toBeInTheDocument();
  });
});

describe('the z colour scale', () => {
  it('leaves anything under one sigma uncoloured', () => {
    // A continuous gradient would make every row look like it was saying something.
    expect(zBand(0.4)).toBe('flat');
    expect(zBand(-0.99)).toBe('flat');
  });

  it('encodes direction and magnitude independently', () => {
    expect(zBand(1.5)).toBe('up-1');
    expect(zBand(-1.5)).toBe('down-1');
    expect(zBand(2.5)).toBe('up-2');
    expect(zBand(4)).toBe('up-3');
    expect(zBand(-4)).toBe('down-3');
  });

  it('has no band for a missing value', () => {
    expect(zBand(null)).toBe('none');
  });
});

describe('the transmission graph', () => {
  it('pulls sign conflicts to the top and explains them', async () => {
    renderTerminal('/terminal/graph');
    expect(await screen.findByText(/1 sign conflict\./)).toBeInTheDocument();
    expect(screen.getByText(/usual channel is not operating/i)).toBeInTheDocument();
  });

  it('calls a zero expected sign regime-dependent, not unknown', async () => {
    renderTerminal('/terminal/graph');
    await screen.findByText(/1 sign conflict\./);
    // expected_sign 0 is a deliberate statement, not missing information.
    expect(screen.getByText('regime-dependent')).toBeInTheDocument();
  });

  it('reports the estimation window beside each estimate', async () => {
    renderTerminal('/terminal/graph');
    await screen.findByText(/1 sign conflict\./);
    // A fixed-window correlation is an average over regimes, not a fact about today.
    expect(screen.getByRole('columnheader', { name: 'window' })).toBeInTheDocument();
  });

  it('lists edges that have no estimate rather than hiding them', async () => {
    renderTerminal('/terminal/graph?as_of=2020-03-16T17:00:00.000Z');
    // Before the night they were estimated, every edge is defined but unmeasured.
    expect(
      await screen.findByText(/Defined but not estimated/),
    ).toBeInTheDocument();
  });
});

describe('the policy path', () => {
  it('says why it is empty rather than drawing a flat line', async () => {
    renderTerminal('/terminal/policy?as_of=2020-03-16T17:00:00.000Z');
    expect(await screen.findByText(/absence of data, not a flat path/i)).toBeInTheDocument();
  });

  it('shows the implied path when one is stored', async () => {
    renderTerminal('/terminal/policy');
    expect(await screen.findByText('2026-10-28')).toBeInTheDocument();
    expect(screen.getByText('-12.5bp')).toBeInTheDocument();
  });
});

describe('the brief', () => {
  it('leads with how many questions could not be answered', async () => {
    renderTerminal('/terminal/brief');
    // Burying this in the prose makes an incomplete brief look complete.
    expect(
      await screen.findByText(/1 of the five standing questions could not be answered/i),
    ).toBeInTheDocument();
  });

  it('renders the markdown as a document, tables included', async () => {
    renderTerminal('/terminal/brief');
    expect(await screen.findByRole('heading', { name: 'What moved' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'series' })).toBeInTheDocument();
    expect(screen.getByText('ust.5y.nominal')).toBeInTheDocument();
  });
});
