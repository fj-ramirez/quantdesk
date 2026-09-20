import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import './index.css';
import { App } from './App';
import { ThemeProvider } from './theme/ThemeContext';

const queryClient = new QueryClient();

/**
 * Start the MSW worker before the app renders, in dev only. T11 (the real API) doesn't
 * exist yet, so without this every query hook would fail immediately. Once T11 ships and
 * `VITE_API_BASE_URL` points at it, set `VITE_ENABLE_MOCKS=false` to talk to the real
 * backend without touching this file. Never runs in a production build.
 */
async function enableMocking() {
  if (!import.meta.env.DEV) return;
  if (import.meta.env.VITE_ENABLE_MOCKS === 'false') return;
  const { worker } = await import('./mocks/browser');
  return worker.start({ onUnhandledRequest: 'bypass' });
}

enableMocking().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </StrictMode>,
  );
});
