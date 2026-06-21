/** Shared day-math helpers. A "day" is the integer count of whole days since
 *  the Unix epoch (UTC), used for the date-range slider and filters. */

/** Whole days since the Unix epoch for the given date. */
export function dateToDay(d: Date): number {
  return Math.floor(d.getTime() / 86400000);
}

/** `Date` at the start (UTC) of the given epoch-day. */
export function dayToDate(day: number): Date {
  return new Date(day * 86400000);
}

/** ISO date string (YYYY-MM-DD) for an epoch-day. */
export function dayToIso(day: number): string {
  return dayToDate(day).toISOString().slice(0, 10);
}

/** Epoch-day for an ISO date string (YYYY-MM-DD), interpreted as UTC midnight. */
export function isoToDay(iso: string): number {
  return dateToDay(new Date(iso + 'T00:00:00Z'));
}

/** Short "Mon D" label for an epoch-day, e.g. "Jun 21". */
export function formatShort(day: number): string {
  return dayToDate(day).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
