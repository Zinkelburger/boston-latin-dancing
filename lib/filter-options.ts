import type { DanceStyle, DayOfWeek } from '@/types/event';

/** Dance styles offered in the filter UI, in display order. */
export const ALL_STYLES: DanceStyle[] = ['bachata', 'salsa', 'kizomba', 'zouk', 'merengue', 'other'];

/** Weekdays in calendar order (Mon–Sun) for the day filter pills. */
export const DAYS: DayOfWeek[] = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

/** Abbreviated day labels for the day filter pills. */
export const DAY_SHORT: Record<DayOfWeek, string> = {
  Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed',
  Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat', Sunday: 'Sun',
};

/** Day name indexed by JS `Date.getDay()` (0 = Sunday). */
export const DAY_NAMES: DayOfWeek[] = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
