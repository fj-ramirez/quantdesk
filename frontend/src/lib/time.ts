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
