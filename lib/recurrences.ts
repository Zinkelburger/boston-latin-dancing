import type { DanceEvent, DayOfWeek } from '@/types/event';
import {
  bostonWeekday,
  bostonStartOfDay,
  bostonTimeOfDayMs,
  dateToDay,
  dayStartMs,
  dayTimeToMs,
  dayToDate,
  isoToDay,
} from '@/lib/dates';

/** Max occurrences shown in upcoming-dates UI (popup, detail page). */
export const UPCOMING_MAX = 3;

/** Day window for expanding recurring events in the feed view. */
export const FEED_RECURRENCE_DAYS = 7;

const DAY_NAMES: DayOfWeek[] = [
  'Sunday', 'Monday', 'Tuesday', 'Wednesday',
  'Thursday', 'Friday', 'Saturday',
];

/** Start of Boston calendar day for a timestamp. */
function startOfDay(ms: number): number {
  return bostonStartOfDay(ms);
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
  const todayDay = dateToDay(new Date());
  const today = dayStartMs(todayDay);
  const end = dayStartMs(todayDay + withinDays);
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
  event: Pick<DanceEvent, 'startDate' | 'schedule' | 'recurrences' | 'nextDateApproximate'>,
  fromMs: number,
  toMs: number,
): boolean {
  return occurrencesInRange(event, fromMs, toMs).length > 0;
}

// Boston epoch-day anchoring "every other week" parity. Must match the naive
// Boston reference in scripts/event_store.py::_matches_schedule_note.
const EVERY_OTHER_REF_DAY = isoToDay('2026-01-02');

function nthWeekdayOfMonth(
  year: number,
  month: number,
  dayOfWeek: DayOfWeek,
  nth: number,
): number | null {
  const targetDow = DAY_NAMES.indexOf(dayOfWeek);
  let count = 0;
  const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  for (let day = 1; day <= lastDay; day++) {
    const ms = Date.UTC(year, month, day, 12);
    if (bostonWeekday(ms) === targetDow) {
      count++;
      if (count === nth) return day;
    }
  }
  return null;
}

function matchesScheduleNote(
  day: number,
  note: string | undefined,
  dayOfWeek: DayOfWeek,
): boolean {
  const noteLower = (note ?? '').toLowerCase();

  const nthMatch = noteLower.match(/(\d)(?:st|nd|rd|th)\s+\w+day/);
  if (nthMatch) {
    const nth = parseInt(nthMatch[1], 10);
    const d = dayToDate(day);
    const target = nthWeekdayOfMonth(
      d.getUTCFullYear(),
      d.getUTCMonth(),
      dayOfWeek,
      nth,
    );
    return target !== null && target === d.getUTCDate();
  }

  if (noteLower.includes('every other') || noteLower.includes('alternating')) {
    const weekNum = Math.floor((day - EVERY_OTHER_REF_DAY) / 7);
    return weekNum % 2 === 0;
  }

  return true;
}

/** Build occurrence ISO on a Boston calendar day, preserving the reference
 *  ISO's Boston wall-clock time. Pinned to Boston: viewer-local Date fields
 *  would shift the day/time for anyone outside Eastern time. */
function occurrenceOnDay(referenceIso: string, day: number): string {
  const timeOfDay = bostonTimeOfDayMs(new Date(referenceIso).getTime());
  return new Date(dayTimeToMs(day, timeOfDay)).toISOString();
}

/** All schedule-based occurrences in [fromMs, toMs] (up to `limit`). */
function scheduleOccurrencesInRange(
  event: Pick<DanceEvent, 'startDate' | 'schedule'>,
  fromMs: number,
  toMs: number,
  limit: number = Infinity,
): string[] {
  const schedule = event.schedule;
  if (!schedule?.length) return [];

  // Iterate whole Boston epoch-days, not instant + 24h: DST transitions make
  // Boston days 23/25 hours long, so fixed-step instants drift off midnight
  // (duplicating the fall-back day and dropping the window's last day).
  const fromDay = dateToDay(new Date(fromMs));
  const toDay = dateToDay(new Date(toMs));
  const out: string[] = [];

  for (let day = fromDay; day <= toDay && out.length < limit; day++) {
    const dayName = DAY_NAMES[dayToDate(day).getUTCDay()];
    for (const entry of schedule) {
      if (entry.dayOfWeek !== dayName) continue;
      if (!matchesScheduleNote(day, entry.note, entry.dayOfWeek)) continue;
      // One occurrence per calendar day even if several entries match.
      out.push(occurrenceOnDay(event.startDate, day));
      break;
    }
  }

  return out;
}

/** First schedule-based occurrence in [fromMs, toMs], or null. */
function firstScheduleOccurrenceInRange(
  event: Pick<DanceEvent, 'startDate' | 'schedule'>,
  fromMs: number,
  toMs: number,
): string | null {
  return scheduleOccurrencesInRange(event, fromMs, toMs, 1)[0] ?? null;
}

type OccurrenceSource = Pick<
  DanceEvent,
  'startDate' | 'schedule' | 'recurrences' | 'nextDateApproximate'
>;

/** Earliest single occurrence in [fromMs, toMs] from recurrences/schedule/startDate. */
function firstOccurrenceFrom(
  event: OccurrenceSource,
  fromMs: number,
  toMs: number,
): string | null {
  if (event.recurrences?.length) {
    return recurrencesInRange(event.recurrences, fromMs, toMs)[0] ?? null;
  }
  if (event.schedule?.length) {
    return firstScheduleOccurrenceInRange(event, fromMs, toMs);
  }
  const eventMs = new Date(event.startDate).getTime();
  return eventMs >= fromMs && eventMs <= toMs ? event.startDate : null;
}

/**
 * The single source of truth for "when does this event happen in [fromMs, toMs]?".
 * Every date-aware consumer (filtering, feed expansion, next-date, display
 * occurrence) derives from this so they can never disagree.
 *
 * Precedence:
 *  1. Approximate (`nextDateApproximate`) — dates are a pattern guess, not
 *     confirmed. Never grid-expand; surface at most ONE next occurrence (from
 *     today onward) so we don't claim it happens on every matching date. The
 *     placement is computed fresh, never the (possibly stale) `startDate`.
 *  2. Concrete `recurrences[]` — authoritative list of occurrence datetimes.
 *  3. `schedule[]` — weekly weekday rule, expanded across the window.
 *  4. Single `startDate` — non-recurring one-off.
 */
export function occurrencesInRange(
  event: OccurrenceSource,
  fromMs: number,
  toMs: number,
): string[] {
  if (event.nextDateApproximate) {
    const start = Math.max(fromMs, startOfDay(Date.now()));
    const next = firstOccurrenceFrom(event, start, toMs);
    return next ? [next] : [];
  }

  if (event.recurrences?.length) {
    return recurrencesInRange(event.recurrences, fromMs, toMs);
  }

  if (event.schedule?.length) {
    return scheduleOccurrencesInRange(event, fromMs, toMs);
  }

  const eventMs = new Date(event.startDate).getTime();
  return eventMs >= fromMs && eventMs <= toMs ? [event.startDate] : [];
}

/** First occurrence start ISO within [fromMs, toMs], or null if none. */
export function firstOccurrenceInRange(
  event: OccurrenceSource,
  fromMs: number,
  toMs: number,
): string | null {
  return occurrencesInRange(event, fromMs, toMs)[0] ?? null;
}

/** End ISO for one occurrence, using the event's original duration. */
export function occurrenceEndDate(
  event: Pick<DanceEvent, 'startDate' | 'endDate'>,
  occurrenceStart: string,
): string {
  const start = new Date(occurrenceStart);
  const eventStart = new Date(event.startDate);
  const eventEnd = new Date(event.endDate);
  // Guard against malformed data where endDate precedes startDate: a negative
  // duration would put every occurrence's end before its start. Clamp to 0.
  const durationMs = Math.max(0, eventEnd.getTime() - eventStart.getTime());
  return new Date(start.getTime() + durationMs).toISOString();
}

type DisplayOccurrenceOpts = {
  displayDate?: string | null;
  fromMs?: number;
  toMs?: number;
};

/** Resolve which occurrence to show in cards/popups for a filtered recurring event. */
export function resolveDisplayOccurrence(
  event: Pick<DanceEvent, 'startDate' | 'endDate' | 'schedule' | 'recurrences' | 'recurring' | 'nextDateApproximate'>,
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
  return DAY_NAMES[bostonWeekday(new Date(iso).getTime())];
}

export function formatRecurrenceDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    timeZone: 'America/New_York',
  });
}

export function formatRecurrenceTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: 'America/New_York',
  });
}

const TZ_OPTS = { timeZone: 'America/New_York' } as const;

const DATE_PARTS_FMT = new Intl.DateTimeFormat('en-US', { ...TZ_OPTS, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });

function dateParts(d: Date): Record<string, string> {
  const p: Record<string, string> = {};
  for (const part of DATE_PARTS_FMT.formatToParts(d)) p[part.type] = part.value;
  return p;
}

/** True when start/end are midnight on the same day (date-only, no time on source). */
export function isDateOnlyEvent(start: string, end: string): boolean {
  const sp = dateParts(new Date(start));
  const ep = dateParts(new Date(end));
  return (
    sp.year === ep.year && sp.month === ep.month && sp.day === ep.day
    && sp.hour === '00' && sp.minute === '00'
    && ep.hour === '00' && ep.minute === '00'
  );
}

/**
 * True for a festival-style listing that spans days with no times on the
 * source: both endpoints land on midnight. The end is exclusive, the way
 * iCalendar writes an all-day DTEND, so the last day people can attend is the
 * day before it. Without this the UI printed "Aug 21 12:00 AM – Aug 24 12:00
 * AM" — hours no source ever published.
 */
export function isMultiDayAllDayEvent(start: string, end: string): boolean {
  const s = new Date(start);
  const e = new Date(end);
  if (!(e.getTime() > s.getTime())) return false;
  const sp = dateParts(s);
  const ep = dateParts(e);
  return sp.hour === '00' && sp.minute === '00' && ep.hour === '00' && ep.minute === '00';
}

/** Inclusive last day of a multi-day all-day range (its exclusive end, minus a day). */
export function allDayLastDay(end: string): Date {
  return new Date(new Date(end).getTime() - 24 * 60 * 60 * 1000);
}

export function formatEventTimeRange(start: string, end: string): string {
  const s = new Date(start);
  const e = new Date(end);
  const dateStr = s.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/New_York',
  });
  if (isDateOnlyEvent(start, end)) return dateStr;

  if (isMultiDayAllDayEvent(start, end)) {
    const last = allDayLastDay(end);
    const lastStr = last.toLocaleDateString('en-US', {
      weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/New_York',
    });
    return lastStr === dateStr ? dateStr : `${dateStr} – ${lastStr}`;
  }

  const startTime = s.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' });
  const endTime = e.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' });

  const sDateStr = s.toLocaleDateString('en-US', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'America/New_York' });
  const eDateStr = e.toLocaleDateString('en-US', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'America/New_York' });
  const sameDay = sDateStr === eDateStr;

  const sameEvening = !sameDay
    && (e.getTime() - s.getTime()) < 12 * 60 * 60 * 1000
    && (() => {
      const p: Record<string, string> = {};
      for (const part of new Intl.DateTimeFormat('en-US', { ...TZ_OPTS, hourCycle: 'h23', hour: '2-digit' }).formatToParts(e)) p[part.type] = part.value;
      return +p.hour < 6;
    })();

  if (sameDay || sameEvening) return `${dateStr}, ${startTime} – ${endTime}`;

  const endDateStr = e.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/New_York',
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
  const endTime = end.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' });
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
  return bostonWeekday(new Date(iso).getTime());
}

function nthWeekdayOrdinalInMonth(iso: string): number {
  const ms = new Date(iso).getTime();
  const dow = bostonWeekday(ms);
  const d = new Date(ms);
  const fmt = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' });
  const p: Record<string, string> = {};
  for (const part of fmt.formatToParts(d)) p[part.type] = part.value;
  const dateOfMonth = +p.day;
  const year = +p.year;
  const month = +p.month - 1;

  let count = 0;
  for (let day = 1; day <= dateOfMonth; day++) {
    if (bostonWeekday(Date.UTC(year, month, day, 12)) === dow) count++;
  }
  return count;
}

function isLastWeekdayOccurrenceInMonth(iso: string): boolean {
  const ms = new Date(iso).getTime();
  const fmt = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' });
  const p: Record<string, string> = {};
  for (const part of fmt.formatToParts(ms)) p[part.type] = part.value;
  const dateOfMonth = +p.day;
  const lastDay = new Date(Date.UTC(+p.year, +p.month, 0)).getUTCDate();
  return dateOfMonth + 7 > lastDay;
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

/**
 * A one-row schedule's note, when it says something the recurrence label does
 * not already say. The popup hides a one-row schedule table (it would just
 * restate the pill and the "Next" line), which would otherwise drop notes like
 * "Lesson + social (18+)" — but not "1st Saturday of each month", which
 * labelFromScheduleNote() has already folded into the label.
 */
export function extraScheduleNote(event: DanceEvent): string | null {
  if (event.schedule?.length !== 1) return null;
  const { note, dayOfWeek } = event.schedule[0];
  if (!note?.trim()) return null;
  return labelFromScheduleNote(note, dayOfWeek) ? null : note;
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
  if (event.nextDateApproximate) return false;
  return !isDenseVenueSchedule(event);
}

const NEXT_SCAN_DAYS = 365;

/** Next occurrence on or after today (from recurrences[] or schedule rules). */
export function nextOccurrenceIso(event: DanceEvent): string | null {
  const fromMs = startOfDay(Date.now());
  const toMs = fromMs + NEXT_SCAN_DAYS * 86400000;
  return occurrencesInRange(event, fromMs, toMs)[0] ?? null;
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
