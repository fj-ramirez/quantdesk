/**
 * Handlers for the routes the shell itself calls, as opposed to any one module's API.
 *
 * Just `GET /health` today, which the launcher's build stamp reads. It lives here rather than
 * in `modules/gex/mocks/handlers.ts` because `/health` is mounted at the root, outside
 * `/api/<module>` — it is the deployment's endpoint, not GEX's (see `app/main.py`).
 */
import { http, HttpResponse } from 'msw';

/** Shaped exactly like a real response: the API reports itself from its environment, the
 * three workers from the files they write at boot. */
export const shellHandlers = [
  http.get('*/health', () =>
    HttpResponse.json({
      status: 'ok',
      provider: 'cboe',
      symbols: ['SPX', 'SPY', 'QQQ', 'GLD', 'DIA'],
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
          build_sha: 'cf59b11',
          build_time: '2026-09-21T20:14:03-04:00',
          started_at: '2026-09-22T00:20:14+00:00',
          label: 'gex-capture: 2026-09-21T20:14:03-04:00 (cf59b11)',
        },
        {
          service: 'research-search',
          build_sha: 'cf59b11',
          build_time: '2026-09-21T20:14:03-04:00',
          started_at: '2026-09-22T00:20:15+00:00',
          label: 'research-search: 2026-09-21T20:14:03-04:00 (cf59b11)',
        },
        {
          service: 'terminal-ingest',
          build_sha: 'cf59b11',
          build_time: '2026-09-21T20:14:03-04:00',
          started_at: '2026-09-22T00:20:13+00:00',
          label: 'terminal-ingest: 2026-09-21T20:14:03-04:00 (cf59b11)',
        },
      ],
    }),
  ),
];
