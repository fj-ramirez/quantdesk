import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { App } from './App';
import { ThemeProvider } from './theme/ThemeContext';

// End-to-end-ish check that URL state actually drives what's on screen, through real
// TanStack Query hooks hitting the MSW handlers (src/mocks) — not just the isolated hook
// test in src/state/urlState.test.tsx.
function renderApp(initialPath: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <App />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe('App', () => {
  it('renders the Dashboard with mocked SPX data by default', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByText(/SPX spot/)).toBeInTheDocument());
  });

  it('clicking the QQQ symbol button updates the URL and reloads QQQ data', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByText(/SPX spot/)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'QQQ' }));

    await waitFor(() => expect(screen.getByText(/QQQ spot/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'QQQ' })).toBeDisabled();
  });

  it('deep link ?symbol=QQQ&filter=ZERO_DTE renders with QQQ pre-selected', async () => {
    renderApp('/?symbol=QQQ&filter=ZERO_DTE');

    expect(screen.getByRole('button', { name: 'QQQ' })).toBeDisabled();
    await waitFor(() => expect(screen.getByText(/QQQ spot/)).toBeInTheDocument());
  });

  it('the QQQ fixture legitimately has a null flip point and the shell does not crash on it', async () => {
    renderApp('/?symbol=QQQ');
    await waitFor(() => expect(screen.getByText(/flip n\/a/)).toBeInTheDocument());
  });

  it('navigating to /history preserves the current symbol in the URL', async () => {
    renderApp('/?symbol=SPY');
    await waitFor(() => expect(screen.getByText(/SPY spot/)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('link', { name: 'History' }));

    await waitFor(() => expect(screen.getByText(/level-history rows loaded for SPY/)).toBeInTheDocument());
  });
});
