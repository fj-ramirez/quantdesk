import { describe, expect, it } from 'vitest';

import { formatBarsThrough, formatFreshness } from './time';

/**
 * T34: `formatFreshness` is the one place the "As of ..." / "At <day>'s close ..." wording
 * lives, shared by the TopBar badge and KeyLevels' footer. It deliberately does no
 * market-hours reasoning of its own -- it only compares `captured_at` to `effective_at`,
 * which the backend (`app.jobs.calendar.effective_data_time`) has already reconciled against
 * the market calendar. These tests therefore pin the *formatting* contract with hand-built
 * timestamps standing in for "backend said this was mid-session" / "backend said this was
 * frozen at a prior close" -- not the calendar logic itself, which belongs to (and is tested
 * in) `backend/tests/test_calendar.py`.
 */
describe('formatFreshness', () => {
  it('mid-session: effective_at equals captured_at, so it reads as a rolling delay', () => {
    // 2026-09-04T15:45:00Z == 11:45 AM ET.
    const text = formatFreshness({
      captured_at: '2026-09-04T15:45:00Z',
      effective_at: '2026-09-04T15:45:00Z',
      delayed_minutes: 15,
    });
    expect(text).toBe('As of 11:45 AM ET · Delayed 15m');
  });

  it('real-time (delayed_minutes: 0) mid-session shows "Real-time", not a delay figure', () => {
    const text = formatFreshness({
      captured_at: '2026-09-04T15:45:00Z',
      effective_at: '2026-09-04T15:45:00Z',
      delayed_minutes: 0,
    });
    expect(text).toBe('As of 11:45 AM ET · Real-time');
  });

  it('post-close: effective_at differs from captured_at, so it reads as the day\'s close, not a rolling delay', () => {
    // The supervisor's own repro: captured 17:55 ET (well after the 16:00 close), but the
    // backend clamped effective_at to 16:15 ET (close + the 15m delay).
    const text = formatFreshness({
      captured_at: '2026-09-04T21:55:00Z', // 17:55 ET
      effective_at: '2026-09-04T20:15:00Z', // 16:15 ET, a Friday
      delayed_minutes: 15,
    });
    expect(text).toBe("At Friday's close (4:15 PM ET)");
    expect(text).not.toMatch(/Delayed/);
  });

  it('a weekend/holiday catch-up (T29) still reads as the prior trading day\'s close', () => {
    // Backend clamp for a Sunday capture: effective_at lands on the prior Friday's close.
    // formatFreshness does not know or care that it's a weekend -- it only sees the two
    // timestamps disagree and names whichever day effective_at falls on.
    const text = formatFreshness({
      captured_at: '2026-09-06T16:00:00Z', // Sunday, vendor payload still "fresh"
      effective_at: '2026-09-04T20:15:00Z', // Friday's close + 15m
      delayed_minutes: 15,
    });
    expect(text).toBe("At Friday's close (4:15 PM ET)");
  });

  it('any difference at all between the two timestamps switches to the close wording', () => {
    const text = formatFreshness({
      captured_at: '2026-09-04T15:45:00.000Z',
      effective_at: '2026-09-04T15:45:01.000Z',
      delayed_minutes: 15,
    });
    expect(text).not.toMatch(/^As of/);
  });
});

// T55: `BarsFreshness`'s date formatter.
describe('formatBarsThrough', () => {
  it('formats a plain calendar date as "<weekday short> <day> <month short>"', () => {
    expect(formatBarsThrough('2026-09-08')).toBe('Tue 8 Sep');
  });

  it('does not shift the date under a non-UTC local timezone', () => {
    // The likeliest failure mode: parsing the bare date as UTC midnight, then formatting in
    // the *local* zone, rolls it back to the previous calendar day west of UTC. Both the
    // parse and the format are pinned to UTC, so this must read Jan 1, not Dec 31.
    expect(formatBarsThrough('2026-01-01')).toBe('Thu 1 Jan');
  });
});
