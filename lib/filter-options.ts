import type { DanceStyle, DayOfWeek } from '@/types/event';

/** Weekdays always visible in the phone day row. Fri–Sun sit behind the arrow.
 *  Kept as a plain Mon-first prefix so the row always reads in calendar order —
 *  revealing the rest appends, it never reshuffles what you were looking at. */
export const PRIMARY_DAYS: DayOfWeek[] = ['Monday', 'Tuesday', 'Wednesday', 'Thursday'];

/** Styles always visible in the phone style row. The rest sit behind the arrow. */
export const PRIMARY_STYLES: DanceStyle[] = ['bachata', 'salsa'];

/** Every dance style that can appear on an event, in display order. */
export const ALL_STYLES: DanceStyle[] = ['bachata', 'salsa', 'kizomba', 'zouk', 'merengue', 'other'];

/** Styles offered in the filter UI. Merengue is always paired with another
 *  style here, so filtering by it never narrows anything — it still shows on
 *  event pills. */
export const FILTER_STYLES: DanceStyle[] = ['bachata', 'salsa', 'kizomba', 'zouk', 'other'];

/** Weekdays in calendar order (Mon–Sun) for the day filter pills. */
export const DAYS: DayOfWeek[] = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

/** Abbreviated day labels for the day filter pills. */
export const DAY_SHORT: Record<DayOfWeek, string> = {
  Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed',
  Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat', Sunday: 'Sun',
};

/** Day name indexed by weekday number (0 = Sunday). Used with bostonWeekday(). */
export const DAY_NAMES: DayOfWeek[] = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
