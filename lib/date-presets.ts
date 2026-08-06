import { dateToDay } from './dates';

export type DatePreset = 'today' | 'tomorrow' | 'weekend' | 'next7' | 'all';

export const PRESET_LABELS: Record<DatePreset, string> = {
  today: 'Today',
  tomorrow: 'Tomorrow',
  weekend: 'This Weekend',
  next7: 'Next 7 Days',
  all: 'All',
};

/** Presets shown as quick-pick chips (excludes the implicit "all"/"any"). */
export const DATE_PRESETS: DatePreset[] = ['today', 'tomorrow', 'weekend', 'next7'];

/** Epoch-day range for a preset, or null for "all" (no date constraint). */
export function computePresetRange(
  preset: DatePreset,
  today: number = dateToDay(new Date()),
): { fromDay: number; toDay: number } | null {
  switch (preset) {
    case 'today':
      return { fromDay: today, toDay: today };
    case 'tomorrow':
      return { fromDay: today + 1, toDay: today + 1 };
    case 'weekend': {
      // Dance weekend is Fri–Sun. Once the weekend has started, show only
      // the remaining days (don't pull a past Friday back in on Sat/Sun).
      const dow = new Date(today * 86400000).getUTCDay();
      if (dow === 0) return { fromDay: today, toDay: today }; // Sunday
      if (dow === 6) return { fromDay: today, toDay: today + 1 }; // Sat–Sun
      if (dow === 5) return { fromDay: today, toDay: today + 2 }; // Fri–Sun
      const daysUntilFri = 5 - dow; // Mon–Thu
      return { fromDay: today + daysUntilFri, toDay: today + daysUntilFri + 2 };
    }
    case 'next7':
      return { fromDay: today, toDay: today + 6 };
    case 'all':
      return null;
  }
}
