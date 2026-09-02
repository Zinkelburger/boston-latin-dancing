import type { DanceStyle, DayOfWeek } from '@/types/event';

/** Every dance style that can appear on an event, in display order. */
export const ALL_STYLES: DanceStyle[] = [
  'bachata',
  'salsa',
  'kizomba',
  'zouk',
  'merengue',
  'other',
];

/** Styles offered in the filter UI. Merengue is always paired with another
 *  style here, so filtering by it never narrows anything — it still shows on
 *  event pills. */
export const FILTER_STYLES: DanceStyle[] = ['bachata', 'salsa', 'kizomba', 'zouk', 'other'];

/** Weekdays in calendar order (Mon–Sun) for the day filter pills. */
export const DAYS: DayOfWeek[] = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];

/** Abbreviated day labels for the day filter pills. */
export const DAY_SHORT: Record<DayOfWeek, string> = {
  Monday: 'Mon',
  Tuesday: 'Tue',
  Wednesday: 'Wed',
  Thursday: 'Thu',
  Friday: 'Fri',
  Saturday: 'Sat',
  Sunday: 'Sun',
};

/** Day name indexed by weekday number (0 = Sunday). Used with bostonWeekday(). */
export const DAY_NAMES: DayOfWeek[] = [
  'Sunday',
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
];
