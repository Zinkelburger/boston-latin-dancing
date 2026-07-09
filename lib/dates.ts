/** Shared day-math helpers. A "day" is the integer count of whole days since
 *  the Unix epoch, identifying a **Boston calendar date** (America/New_York).
 *  Used for the date-range slider and filters.
 *
 *  Everything is pinned to Boston time on purpose: these are all Boston events,
 *  and the app must bucket dates the same way whether it runs in the browser,
 *  on a Vercel server (which runs in UTC), or for a visitor in another timezone.
 *
 *  An epoch-day is represented canonically as UTC-midnight of that calendar
 *  date, so converting to/from a YYYY-MM-DD string stays trivial. Use
 *  `dayStartMs` whenever you need the real absolute instant a Boston day begins. */

const TZ = 'America/New_York';

/** Offset in ms between Boston wall-clock and UTC at `instant` (negative; e.g.
 *  -4h in summer, -5h in winter). `wallClockAsUTC = instant + offset`. */
function bostonOffsetMs(instant: number): number {
  const dtf = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  const p: Record<string, string> = {};
  for (const part of dtf.formatToParts(instant)) p[part.type] = part.value;
  const asUTC = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
  return asUTC - instant;
}

/** 0-6 weekday index (Sun=0) for an instant interpreted in Boston time. */
export function bostonWeekday(ms: number): number {
  const shifted = ms + bostonOffsetMs(ms);
  return new Date(shifted).getUTCDay();
}

/** Start-of-day (Boston midnight) as absolute epoch-ms for an arbitrary instant. */
export function bostonStartOfDay(ms: number): number {
  return dayStartMs(dateToDay(new Date(ms)));
}

/** Epoch-day of the Boston calendar date that contains `d`. */
export function dateToDay(d: Date): number {
  const ms = d.getTime();
  return Math.floor((ms + bostonOffsetMs(ms)) / 86400000);
}

/** Canonical `Date` (UTC midnight) standing in for an epoch-day. For display
 *  or ISO extraction only — not a real instant. Use `dayStartMs` for that. */
export function dayToDate(day: number): Date {
  return new Date(day * 86400000);
}

/** Absolute ms at the moment a Boston calendar day begins (local midnight). */
export function dayStartMs(day: number): number {
  const utcMidnight = day * 86400000;
  return utcMidnight - bostonOffsetMs(utcMidnight);
}

/** Boston wall-clock milliseconds since local midnight for an instant. */
export function bostonTimeOfDayMs(ms: number): number {
  const wall = ms + bostonOffsetMs(ms);
  return ((wall % 86400000) + 86400000) % 86400000;
}

/** Absolute ms for a Boston wall-clock time-of-day on an epoch-day. Unlike
 *  `dayStartMs(day) + timeOfDay`, this stays correct on 23/25-hour DST days. */
export function dayTimeToMs(day: number, timeOfDayMs: number): number {
  const wallAsUTC = day * 86400000 + timeOfDayMs;
  const guess = wallAsUTC - bostonOffsetMs(wallAsUTC);
  return wallAsUTC - bostonOffsetMs(guess);
}

/** ISO date string (YYYY-MM-DD) for an epoch-day. */
export function dayToIso(day: number): string {
  return dayToDate(day).toISOString().slice(0, 10);
}

/** Epoch-day for an ISO date string (YYYY-MM-DD). */
export function isoToDay(day: string): number {
  return Math.floor(new Date(day + 'T00:00:00Z').getTime() / 86400000);
}

/** Short "Mon D" label for an epoch-day, e.g. "Jun 21". */
export function formatShort(day: number): string {
  return dayToDate(day).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });
}
