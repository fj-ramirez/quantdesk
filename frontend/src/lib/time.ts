/**
 * The backend stores every timestamp tz-aware in UTC (docs/schema.md); this user thinks in
 * America/New_York market hours (PLAN.md `.env.example`: `TZ=America/New_York`). Every
 * display of a snapshot time must go through here so "UTC on the wire, NY on screen" is
 * enforced in one place rather than re-derived per component.
 */
const NY_TIME_ZONE = 'America/New_York';

const timeFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: NY_TIME_ZONE,
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
});

const dateTimeFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: NY_TIME_ZONE,
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
});

const weekdayFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: NY_TIME_ZONE,
  weekday: 'long',
});

/** e.g. "11:45 AM ET". Appends "ET" literally rather than relying on `timeZoneName`, whose
 * abbreviation flips between EST/EDT and would otherwise need its own explanation. */
export function formatNyTime(isoUtc: string): string {
  return `${timeFormatter.format(new Date(isoUtc))} ET`;
}

/** e.g. "Sep 4, 11:45 AM ET" — for the snapshot selector, where the date matters too. */
export function formatNyDateTime(isoUtc: string): string {
  return `${dateTimeFormatter.format(new Date(isoUtc))} ET`;
}

/** e.g. "Delayed 15m" / "Real-time" — `delayed_minutes` is a Cboe-feed entitlement fact
 * (0 = real-time, 15 = the free delayed JSON), not something to infer from the clock. */
export function formatDelay(delayedMinutes: number): string {
  return delayedMinutes <= 0 ? 'Real-time' : `Delayed ${delayedMinutes}m`;
}

/**
 * T34: the single "As of ..." wording shared by the TopBar freshness badge and KeyLevels'
 * footer, so the post-close fix lives in exactly one place rather than being reimplemented
 * per component.
 *
 * `captured_at` is the vendor's raw payload timestamp; `effective_at` (`SnapshotInfo`,
 * `app.jobs.calendar.effective_data_time`) is the backend's own honest "as of" instant,
 * already clamped to the last market close when the market was shut at capture time. This
 * function does no market-hours reasoning itself — it only compares the two timestamps the
 * backend already computed, which is what keeps that logic out of the frontend entirely
 * (T34's brief: "the frontend must not have to reimplement market-hours logic").
 *
 * - `effective_at === captured_at` (regular session): "As of 11:45 AM ET · Delayed 15m" —
 *   unchanged from the pre-T34 wording, since a rolling delay figure is honest while the
 *   market is open.
 * - Otherwise (the data was frozen at a prior close when captured): "At Friday's close
 *   (4:00 PM ET)" — a fixed instant, not a rolling "delayed Nm" that keeps looking fresher
 *   than it is the longer the page sits open in the evening.
 */
export function formatFreshness(snapshot: { captured_at: string; effective_at: string; delayed_minutes: number }): string {
  const { captured_at, effective_at, delayed_minutes } = snapshot;
  if (new Date(effective_at).getTime() === new Date(captured_at).getTime()) {
    return `As of ${formatNyTime(captured_at)} · ${formatDelay(delayed_minutes)}`;
  }
  const weekday = weekdayFormatter.format(new Date(effective_at));
  return `At ${weekday}'s close (${formatNyTime(effective_at)})`;
}

// `timeZone: 'UTC'` on all three is deliberate and load-bearing, not a default worth
// dropping -- see `formatBarsThrough` below for why.
const barsWeekdayFormatter = new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: 'UTC' });
const barsDayFormatter = new Intl.DateTimeFormat('en-US', { day: 'numeric', timeZone: 'UTC' });
const barsMonthFormatter = new Intl.DateTimeFormat('en-US', { month: 'short', timeZone: 'UTC' });

/**
 * T55: `"2026-09-08"` -> `"Tue 8 Sep"`, for `BarsFreshness`'s "Bars through ..." summary.
 *
 * `last_bar_date` (`app/api/health.py`'s `SymbolBarsHealth`) is a plain calendar date, not a
 * UTC instant like `captured_at` elsewhere in this app -- there is no time-of-day component
 * to convert to New York time, and treating it as one anyway (`new Date(iso)` parses a bare
 * date as UTC midnight, then a *local*-zone formatter renders it) risks rendering the
 * *previous* calendar day in a timezone west of UTC. The date is built with `Date.UTC` from
 * the parsed y/m/d fields and every formatter below is pinned to `timeZone: 'UTC'` to match,
 * so the calendar date displayed is exactly the one the API sent. Day and month are formatted
 * separately and joined in a fixed order rather than as one combined `{day, month}`
 * formatter, because `en-US`'s combined ordering is "Sep 8" (month first) -- not the "8 Sep"
 * 07-ui.md's own example ("Bars through Mon 8 Sep · 47 symbols") specifies.
 */
export function formatBarsThrough(dateIso: string): string {
  const [year, month, day] = dateIso.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return `${barsWeekdayFormatter.format(date)} ${barsDayFormatter.format(date)} ${barsMonthFormatter.format(date)}`;
}
