import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { BuildStamp } from './BuildStamp';
import { server } from '../mocks/server';

/**
 * The build stamp's whole reason for existing is a half-finished deploy, so the tests are
 * about exactly that: every service reported separately, and the frontend's own line coming
 * from the bundle rather than from the API.
 */
describe('BuildStamp', () => {
  it('lists every service the API reports, plus the frontend itself', async () => {
    render(<BuildStamp />);

    // The frontend's line is available immediately -- it is compiled in, not fetched.
    expect(screen.getByText('frontend')).toBeInTheDocument();

    await waitFor(() => expect(screen.getByText('backend')).toBeInTheDocument());
    for (const service of ['gex-capture', 'research-search', 'terminal-ingest']) {
      expect(screen.getByText(service)).toBeInTheDocument();
    }
    expect(screen.getAllByText('cf59b11')).toHaveLength(4);
  });

  it('shows a service on an older build as an older build, not as an error', async () => {
    server.use(
      http.get('*/health', () =>
        HttpResponse.json({
          status: 'ok',
          db: 'ok',
          services: [
            {
              service: 'backend',
              build_sha: 'cf59b11',
              build_time: '2026-09-21T20:14:03-04:00',
              started_at: '2026-09-22T00:20:11+00:00',
              label: 'backend: 2026-09-21T20:14:03-04:00 (cf59b11)',
            },
            {
              service: 'gex-capture',
              build_sha: '0cb3751',
              build_time: '2026-09-20T11:02:44-04:00',
              started_at: '2026-09-21T15:49:02+00:00',
              label: 'gex-capture: 2026-09-20T11:02:44-04:00 (0cb3751)',
            },
          ],
        }),
      ),
    );

    render(<BuildStamp />);

    await waitFor(() => expect(screen.getByText('0cb3751')).toBeInTheDocument());
    expect(screen.getByText('cf59b11')).toBeInTheDocument();
  });

  it('still reports the frontend when /health cannot be reached', async () => {
    server.use(http.get('*/health', () => HttpResponse.error()));

    render(<BuildStamp />);

    // No error banner, no blank space: the one line it knows for certain. A failed probe is
    // most likely to be the deploy this component exists to diagnose.
    await waitFor(() => expect(screen.getByText('frontend')).toBeInTheDocument());
    expect(screen.queryByText('backend')).not.toBeInTheDocument();
  });

  it('renders an unstamped build as "unknown" rather than inventing one', async () => {
    server.use(
      http.get('*/health', () =>
        HttpResponse.json({
          status: 'ok',
          db: 'ok',
          services: [
            {
              service: 'backend',
              build_sha: 'unknown',
              build_time: 'unknown',
              started_at: 'unknown',
              label: 'backend: unknown (unknown)',
            },
          ],
        }),
      ),
    );

    render(<BuildStamp />);

    // Two: the backend's, and the frontend's own -- the test bundle is built without a stamp,
    // which is exactly what a local `npm run dev` produces and must not read as a date.
    await waitFor(() => expect(screen.getAllByText('unknown').length).toBeGreaterThan(1));
    expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument();
  });
});
