import type { DanceEvent, DayOfWeek } from '@/types/event';

/** Max occurrences shown in upcoming-dates UI (popup, detail page). */
export const UPCOMING_MAX = 3;

/** Day window for expanding recurring events in the feed view. */
export const FEED_RECURRENCE_DAYS = 7;

const DAY_NAMES: DayOfWeek[] = [
  'Sunday', 'Monday', 'Tuesday', 'Wednesday',
  'Thursday', 'Friday', 'Saturday',
];

/** Start of local calendar day for a timestamp. */
function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** Next `maxCount` recurrence ISO strings on or after today. */
export function upcomingRecurrences(
  dates: string[],
  maxCount: number = UPCOMING_MAX,
): string[] {
  const today = startOfDay(Date.now());

  return dates
    .filter(iso => new Date(iso).getTime() >= today)
    .sort((a, b) => new Date(a).getTime() - new Date(b).getTime())
    .slice(0, maxCount);
}

/** Recurrence ISO strings from today through the next `withinDays` days (feed expansion). */
export function recurrencesWithinDays(
  dates: string[],
  withinDays: number = FEED_RECURRENCE_DAYS,
): string[] {
  const today = startOfDay(Date.now());
  const end = today + withinDays * 86400000;
  return recurrencesInRange(dates, today, end - 1);
}

/** Recurrence ISO strings whose start time falls within [fromMs, toMs] (inclusive). */
export function recurrencesInRange(
  dates: string[],
  fromMs: number,
  toMs: number,
): string[] {
  return dates
    .filter(iso => {
      const ms = new Date(iso).getTime();
      return ms >= fromMs && ms <= toMs;
    })
    .sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
}

/** Whether an event has any occurrence in the map/feed date window. */
export function eventMatchesDateRange(
  event: Pick<DanceEvent, 'startDate' | 'schedule' | 'recurrences'>,
  fromMs: number,
  toMs: number,
): boolean {
  if (event.schedule?.length) return true;

  if (event.recurrences?.length) {
    return recurrencesInRange(event.recurrences, fromMs, toMs).length > 0;
  }

  const eventMs = new Date(event.startDate).getTime();
  return eventMs >= fromMs && eventMs <= toMs;
}

const EVERY_OTHER_REF_MS = new Date('2026-01-02T00:00:00').getTime();

function nthWeekdayOfMonth(
  year: number,
  month: number,
  dayOfWeek: DayOfWeek,
  nth: number,
): Date | null {
  const targetDow = DAY_NAMES.indexOf(dayOfWeek);
  let count = 0;
  const lastDay = new Date(year, month + 1, 0).getDate();
  for (let day = 1; day <= lastDay; day++) {
    const d = new Date(year, month, day);
    if (d.getDay() === targetDow) {
      count++;
      if (count === nth) return d;
    }
  }
  return null;
}

function matchesScheduleNote(
  date: Date,
  note: string | undefined,
  dayOfWeek: DayOfWeek,
): boolean {
  const noteLower = (note ?? '').toLowerCase();

  const nthMatch = noteLower.match(/(\d)(?:st|nd|rd|th)\s+\w+day/);
  if (nthMatch) {
    const nth = parseInt(nthMatch[1], 10);
    const target = nthWeekdayOfMonth(
      date.getFullYear(),
      date.getMonth(),
      dayOfWeek,
      nth,
    );
    return (
      target !== null
      && target.getDate() === date.getDate()
      && target.getMonth() === date.getMonth()
    );
  }

  if (noteLower.includes('every other') || noteLower.includes('alternating')) {
    const dayMs = startOfDay(date.getTime());
    const weekNum = Math.floor((dayMs - EVERY_OTHER_REF_MS) / (7 * 86400000));
    return weekNum % 2 === 0;
  }

  return true;
}

/** Build occurrence ISO on a calendar day, preserving time from a reference ISO. */
function occurrenceOnDay(referenceIso: string, dayMs: number): string {
  const ref = new Date(referenceIso);
  const day = new Date(dayMs);
  const result = new Date(ref);
  result.setFullYear(day.getFullYear(), day.getMonth(), day.getDate());
  return result.toISOString();
}

/** First schedule-based occurrence in [fromMs, toMs] when recurrences are absent. */
function firstScheduleOccurrenceInRange(
  event: Pick<DanceEvent, 'startDate' | 'schedule'>,
  fromMs: number,
  toMs: number,
): string | null {
  const schedule = event.schedule;
  if (!schedule?.length) return null;

  const fromDay = startOfDay(fromMs);
  const toDay = startOfDay(toMs);

  for (let day = fromDay; day <= toDay; day += 86400000) {
    const d = new Date(day);
    const dayName = DAY_NAMES[d.getDay()];
    for (const entry of schedule) {
      if (entry.dayOfWeek !== dayName) continue;
      if (!matchesScheduleNote(d, entry.note, entry.dayOfWeek)) continue;
      return occurrenceOnDay(event.startDate, day);
    }
  }

  return null;
}

/** First occurrence start ISO within [fromMs, toMs], or null if none. */
export function firstOccurrenceInRange(
  event: Pick<DanceEvent, 'startDate' | 'schedule' | 'recurrences'>,
  fromMs: number,
  toMs: number,
): string | null {
  if (event.recurrences?.length) {
    const inRange = recurrencesInRange(event.recurrences, fromMs, toMs);
    return inRange[0] ?? null;
  }

  if (event.schedule?.length) {
    return firstScheduleOccurrenceInRange(event, fromMs, toMs);
  }

  const eventMs = new Date(event.startDate).getTime();
  if (eventMs >= fromMs && eventMs <= toMs) return event.startDate;
  return null;
}

/** End ISO for one occurrence, using the event's original duration. */
export function occurrenceEndDate(
  event: Pick<DanceEvent, 'startDate' | 'endDate'>,
  occurrenceStart: string,
): string {
  const start = new Date(occurrenceStart);
  const eventStart = new Date(event.startDate);
  const eventEnd = new Date(event.endDate);
  const durationMs = eventEnd.getTime() - eventStart.getTime();
  return new Date(start.getTime() + durationMs).toISOString();
}

type DisplayOccurrenceOpts = {
  displayDate?: string | null;
  fromMs?: number;
  toMs?: number;
};

/** Resolve which occurrence to show in cards/popups for a filtered recurring event. */
export function resolveDisplayOccurrence(
  event: Pick<DanceEvent, 'startDate' | 'endDate' | 'schedule' | 'recurrences' | 'recurring'>,
  opts: DisplayOccurrenceOpts = {},
): { start: string; end: string } {
  const { displayDate, fromMs, toMs } = opts;

  let start = event.startDate;
  if (displayDate) {
    start = displayDate;
  } else if (event.recurring && fromMs != null && toMs != null) {
    const first = firstOccurrenceInRange(event, fromMs, toMs);
    if (first) start = first;
  }

  return { start, end: occurrenceEndDate(event, start) };
}

export function dayOfWeekFromIso(iso: string): DayOfWeek {
  return DAY_NAMES[new Date(iso).getDay()];
}

export function formatRecurrenceDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

export function formatRecurrenceTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  });
}

/** True when start/end are midnight on the same day (date-only, no time on source). */
export function isDateOnlyEvent(start: string, end: string): boolean {
  const s = new Date(start);
  const e = new Date(end);
  return (
    s.toDateString() === e.toDateString()
    && s.getHours() === 0 && s.getMinutes() === 0
    && e.getHours() === 0 && e.getMinutes() === 0
  );
}

export function formatEventTimeRange(start: string, end: string): string {
  const s = new Date(start);
  const e = new Date(end);
  const dateStr = s.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });
  if (isDateOnlyEvent(start, end)) return dateStr;

  const startTime = s.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  const endTime = e.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

  const sameDay = s.toDateString() === e.toDateString();
  const sameEvening = !sameDay
    && (e.getTime() - s.getTime()) < 12 * 60 * 60 * 1000
    && e.getHours() < 6;

  if (sameDay || sameEvening) return `${dateStr}, ${startTime} – ${endTime}`;

  const endDateStr = e.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });
  return `${dateStr} ${startTime} – ${endDateStr} ${endTime}`;
}

/** Time range for one occurrence, using schedule entry or event duration. */
export function recurrenceTimeRange(event: DanceEvent, recurrenceIso: string): string {
  const day = dayOfWeekFromIso(recurrenceIso);
  const scheduleEntry = event.schedule?.find(s => s.dayOfWeek === day);
  if (scheduleEntry?.time) return scheduleEntry.time;

  const start = new Date(recurrenceIso);
  const eventStart = new Date(event.startDate);
  const eventEnd = new Date(event.endDate);
  const durationMs = eventEnd.getTime() - eventStart.getTime();
  const end = new Date(start.getTime() + durationMs);

  const startTime = formatRecurrenceTime(recurrenceIso);
  const endTime = end.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  return `${startTime} – ${endTime}`;
}

/** Day + time slot key for one occurrence (used to detect repeating patterns). */
export function recurrenceSlotKey(event: DanceEvent, recurrenceIso: string): string {
  const day = dayOfWeekFromIso(recurrenceIso);
  return `${day}|${recurrenceTimeRange(event, recurrenceIso)}`;
}

const DAY_SHORT_LIST = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;
const ORDINAL_WORDS: Record<number, string> = {
  1: 'First', 2: 'Second', 3: 'Third', 4: 'Fourth', 5: 'Fifth',
};

function weekdayIndex(iso: string): number {
  return new Date(iso).getDay();
}

function nthWeekdayOrdinalInMonth(iso: string): number {
  const d = new Date(iso);
  let count = 0;
  for (let day = 1; day <= d.getDate(); day++) {
    const probe = new Date(d.getFullYear(), d.getMonth(), day);
    if (probe.getDay() === d.getDay()) count++;
  }
  return count;
}

function isLastWeekdayOccurrenceInMonth(iso: string): boolean {
  const d = new Date(iso);
  const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
  return d.getDate() + 7 > lastDay;
}

function ordinalPhrase(nth: number, isLast: boolean): string {
  if (isLast) return 'Last';
  return ORDINAL_WORDS[nth] ?? `${nth}th`;
}

function medianGapDays(dates: string[]): number | null {
  if (dates.length < 2) return null;
  const gaps: number[] = [];
  for (let i = 1; i < dates.length; i++) {
    gaps.push(
      (new Date(dates[i]).getTime() - new Date(dates[i - 1]).getTime()) / 86400000,
    );
  }
  gaps.sort((a, b) => a - b);
  const mid = Math.floor(gaps.length / 2);
  return gaps.length % 2 ? gaps[mid] : (gaps[mid - 1] + gaps[mid]) / 2;
}

function labelFromScheduleNote(note: string | undefined, dayName: DayOfWeek): string | null {
  const noteLower = (note ?? '').toLowerCase();
  if (noteLower.includes('every other') || noteLower.includes('alternating')) {
    return `Every other ${dayName}`;
  }
  const nthMatch = noteLower.match(/(\d)(?:st|nd|rd|th)\s+\w+day/);
  if (nthMatch || /\b1st\b/.test(noteLower)) {
    const nth = nthMatch ? parseInt(nthMatch[1], 10) : 1;
    const word = ORDINAL_WORDS[nth] ?? `${nth}th`;
    return `${word} ${dayName} of each month`;
  }
  if (noteLower.includes('of each month') || noteLower.includes('of the month')) {
    return `${dayName}s monthly`;
  }
  return null;
}

function compactScheduleDays(schedule: NonNullable<DanceEvent['schedule']>): string {
  const indices = [...new Set(schedule.map(s => DAY_NAMES.indexOf(s.dayOfWeek)))].sort(
    (a, b) => a - b,
  );
  if (indices.length === 7) return 'Every night';

  const segments: string[] = [];
  let i = 0;
  while (i < indices.length) {
    const start = indices[i];
    let j = i;
    while (j + 1 < indices.length && indices[j + 1] === indices[j] + 1) j++;
    segments.push(
      j === i
        ? DAY_SHORT_LIST[start]
        : `${DAY_SHORT_LIST[start]}–${DAY_SHORT_LIST[indices[j]]}`,
    );
    i = j + 1;
  }
  return segments.join(', ');
}

function labelFromSchedule(schedule: NonNullable<DanceEvent['schedule']>): string {
  if (schedule.length === 1) {
    const { dayOfWeek, time, note } = schedule[0];
    const fromNote = labelFromScheduleNote(note, dayOfWeek);
    if (fromNote) return fromNote;
    return time ? `Every ${dayOfWeek} · ${time}` : `Every ${dayOfWeek}`;
  }
  const compact = compactScheduleDays(schedule);
  return schedule.length >= 4 ? `${compact} · see schedule` : compact;
}

function labelFromRecurrenceDates(recurrences: string[]): string | null {
  const sorted = [...recurrences].sort(
    (a, b) => new Date(a).getTime() - new Date(b).getTime(),
  );
  if (sorted.length < 2) return null;

  const weekdays = new Set(sorted.map(weekdayIndex));
  if (weekdays.size !== 1) return null;

  const dayName = DAY_NAMES[weekdayIndex(sorted[0])];
  const nthValues = sorted.map(nthWeekdayOrdinalInMonth);
  const lastFlags = sorted.map(isLastWeekdayOccurrenceInMonth);
  const gap = medianGapDays(sorted);

  if (
    new Set(nthValues).size === 1
    && new Set(lastFlags).size === 1
    && gap !== null
    && gap >= 24
    && gap <= 35
  ) {
    return `${ordinalPhrase(nthValues[0], lastFlags[0])} ${dayName} of each month`;
  }
  if (gap !== null && gap >= 6 && gap <= 8) return `Every ${dayName}`;
  if (gap !== null && gap >= 13 && gap <= 15) return `Every other ${dayName}`;
  return null;
}

/** Infer recurrence label (mirrors scripts/recurrence_utils.py). */
export function computeRecurrenceLabel(event: DanceEvent): string | null {
  if (!event.recurring) return null;

  if (event.schedule?.length) {
    return labelFromSchedule(event.schedule);
  }

  const recurrences = event.recurrences ?? [];
  if (recurrences.length >= 2) {
    const fromDates = labelFromRecurrenceDates(recurrences);
    if (fromDates) return fromDates;
  }

  if (event.dayOfWeek && recurrences.length <= 1) {
    return `Every ${event.dayOfWeek}`;
  }

  return null;
}

/** Published label or computed fallback. */
export function getRecurrenceLabel(event: DanceEvent): string | null {
  if (event.recurrenceLabel) return event.recurrenceLabel;
  return computeRecurrenceLabel(event);
}

/** Multi-day venue hubs (e.g. Havana) — pattern only, no single "next" date. */
export function isDenseVenueSchedule(event: DanceEvent): boolean {
  const label = getRecurrenceLabel(event);
  if (label?.includes('see schedule') || label === 'Every night') return true;
  return (event.schedule?.length ?? 0) >= 3;
}

/** Sparse weekly/biweekly/monthly patterns where the next date is useful. */
export function shouldShowNextOccurrence(event: DanceEvent): boolean {
  if (!event.recurring || !getRecurrenceLabel(event)) return false;
  return !isDenseVenueSchedule(event);
}

const NEXT_SCAN_DAYS = 365;

/** Next occurrence on or after today (from recurrences[] or schedule rules). */
export function nextOccurrenceIso(event: DanceEvent): string | null {
  const fromMs = startOfDay(Date.now());
  const toMs = fromMs + NEXT_SCAN_DAYS * 86400000;

  const fromRec = upcomingRecurrences(event.recurrences ?? [], 1)[0];
  if (fromRec) return fromRec;

  if (event.schedule?.length) {
    return firstScheduleOccurrenceInRange(event, fromMs, toMs);
  }

  if (new Date(event.startDate).getTime() >= fromMs) return event.startDate;
  return null;
}

/**
 * Whether the upcoming-dates table adds information beyond a known pattern.
 */
export function shouldShowUpcomingDates(event: DanceEvent): boolean {
  if (getRecurrenceLabel(event)) return false;

  const dates = upcomingRecurrences(event.recurrences ?? []);
  if (dates.length === 0) return false;

  if (event.schedule && event.schedule.length > 0) {
    const allMapToSchedule = dates.every(iso => {
      const day = dayOfWeekFromIso(iso);
      return event.schedule!.some(s => s.dayOfWeek === day);
    });
    if (allMapToSchedule) return false;
  }

  const slots = dates.map(iso => recurrenceSlotKey(event, iso));
  if (dates.length >= UPCOMING_MAX && new Set(slots).size === 1) return false;

  return true;
}

/** Short when-label for cards and list views. */
export function recurringWhenLabel(event: DanceEvent): string | null {
  if (!event.recurring) return null;

  const label = getRecurrenceLabel(event);
  if (label) {
    if (shouldShowNextOccurrence(event)) {
      const next = nextOccurrenceIso(event);
      if (next) return `${label} · Next: ${formatRecurrenceDate(next)}`;
    }
    return label;
  }

  const dates = upcomingRecurrences(event.recurrences ?? []);
  if (dates.length === 0) return null;

  if (shouldShowUpcomingDates(event)) {
    return dates.map(formatRecurrenceDate).join(' · ');
  }

  const slot = recurrenceSlotKey(event, dates[0]);
  const [day] = slot.split('|');
  return `Every ${day}`;
}
