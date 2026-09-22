/**
 * Which build every service in this deployment is running (`GET /health`).
 *
 * It exists because of a specific failure on 2026-09-21: one `docker compose up -d --build`
 * rebuilt four containers and failed on the fifth, leaving the stack half-updated with
 * nothing anywhere saying so. So this reports **per service**, never one number for "the
 * stack" — a single version would have looked perfectly healthy that day.
 *
 * The frontend's own line does not come from the API. It is inlined into this bundle at build
 * time (`VITE_BUILD_SHA`, see `frontend/Dockerfile`), because a static bundle behind nginx has
 * no database and no shared volume, and asking the backend what the frontend is would be the
 * backend guessing. When the two lines disagree, that is the answer, not a glitch.
 *
 * Failure is quiet by design. This is diagnostic furniture at the bottom of the launcher: if
 * `/health` cannot be reached it still renders the one line it knows for certain, rather than
 * putting an error banner on the first page of the app.
 */
import { useEffect, useState } from 'react';
import { apiFetch } from '../lib/http';

/** What the build did not tell us. Matches `app.core.version.UNKNOWN`. */
const UNKNOWN = 'unknown';

export interface ServiceVersion {
  service: string;
  build_sha: string;
  build_time: string;
  started_at: string;
  /** `backend: 2026-09-21T20:14:03-04:00 (cf59b11)`, built server-side. */
  label: string;
}

interface HealthResponse {
  status: string;
  db: string;
  services?: ServiceVersion[];
}

const FRONTEND_SHA = (import.meta.env.VITE_BUILD_SHA as string | undefined) || UNKNOWN;
const FRONTEND_TIME = (import.meta.env.VITE_BUILD_TIME as string | undefined) || UNKNOWN;

/** This bundle's own stamp, inlined by Vite at build time. */
export const FRONTEND_BUILD: ServiceVersion = {
  service: 'frontend',
  build_sha: FRONTEND_SHA,
  build_time: FRONTEND_TIME,
  started_at: UNKNOWN,
  label: `frontend: ${FRONTEND_TIME} (${FRONTEND_SHA})`,
};

/** `2026-09-21T20:14:03-04:00` → `2026-09-21 20:14`, and anything unparseable through
 * untouched — an `unknown` must stay `unknown` rather than becoming `Invalid Date`. */
function shortTime(value: string): string {
  if (!value || value === UNKNOWN) return UNKNOWN;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function BuildStamp() {
  const [services, setServices] = useState<ServiceVersion[] | null>(null);

  useEffect(() => {
    let live = true;
    apiFetch<HealthResponse>('/health')
      .then((health) => {
        if (live) setServices(health.services ?? []);
      })
      .catch(() => {
        // Deliberately swallowed: see the module comment. The frontend line below still
        // renders, and it is the line a half-failed deploy is most likely to be about.
        if (live) setServices([]);
      });
    return () => {
      live = false;
    };
  }, []);

  const rows: ServiceVersion[] = [FRONTEND_BUILD, ...(services ?? [])];

  return (
    <section className="lx-build" aria-label="Build versions">
      <h2 className="lx-build__title">Build</h2>
      <ul className="lx-build__list">
        {rows.map((row) => (
          <li key={row.service} className="lx-build__row">
            <span className="lx-build__service">{row.service}</span>
            <span className="lx-build__time">{shortTime(row.build_time)}</span>
            <code className="lx-build__sha">{row.build_sha}</code>
          </li>
        ))}
      </ul>
    </section>
  );
}
