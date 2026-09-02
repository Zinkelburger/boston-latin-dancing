/**
 * Occurrence math and date labels for published events.
 *
 * scripts/event_store.py::publish() is the source of truth for recurrence.
 * Every published event that recurs carries a concrete `recurrences[]` list
 * (weekly `schedule[]` rules are expanded there, with exclusions and notes
 * applied) and a `recurrenceLabel`. Nothing here re-derives either: the
 * previous TypeScript mirrors of the Python schedule expansion and label
 * inference had drifted (fractional vs. truncated median gaps, per-entry
 * times), so the UI could disagree with the data it was rendering. If a
 * label or occurrence looks wrong, fix publish() and republish.
 */
import type { DanceEvent, DayOfWeek } from '@/types/event';
import { bostonWeekday, bostonStartOfDay, hasStartDate } from '@/lib/dates';
import { DAY_NAMES } from '@/lib/filter-options';

/** Max occurrences shown in upcoming-dates UI (popup, detail page). */
export const UPCOMING_MAX = 3;

/** Next `maxCount` recurrence ISO strings on or after today. */
export function upcomingRecurrences(
  dates: string[],
  maxCount: number = UPCOMING_MAX,
): string[] {
  const today = bostonStartOfDay(Date.now());

  return dates
    .filter(iso => new Date(iso).getTime() >= today)
    .sort((a, b) => new Date(a).getTime() - new Date(b).getTime())
    .slice(0, maxCount);
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

type OccurrenceSource = Pick<
  DanceEvent,
  'startDate' | 'recurrences' | 'nextDateApproximate'
>;

/** Whether an event has any occurrence in the map/feed date window. */
export function eventMatchesDateRange(
  event: OccurrenceSource,
  fromMs: number,
  toMs: number,
): boolean {
  return occurrencesInRange(event, fromMs, toMs).length > 0;
}

/**
 * The single source of truth for "when does this event happen in [fromMs, toMs]?".
 * Every date-aware consumer (filtering, feed expansion, next-date, display
 * occurrence) derives from this so they can never disagree.
 *
 * Precedence:
 *  1. Approximate (`nextDateApproximate`) — dates are a pattern guess, not
 *     confirmed. Surface at most ONE next occurrence (from today onward) so we
 *     don't claim it happens on every matching date.
 *  2. Concrete `recurrences[]` — the authoritative list from publish().
 *  3. Single `startDate` — non-recurring one-off.
 */
export function occurrencesInRange(
  event: OccurrenceSource,
  fromMs: number,
  toMs: number,
): string[] {
  const from = event.nextDateApproximate
    ? Math.max(fromMs, bostonStartOfDay(Date.now()))
    : fromMs;

  let all: string[];
  if (event.recurrences?.length) {
    all = recurrencesInRange(event.recurrences, from, toMs);
  } else if (hasStartDate(event)) {
    all = recurrencesInRange([event.startDate], from, toMs);
  } else {
    all = [];
  }

  return event.nextDateApproximate ? all.slice(0, 1) : all;
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
  event: Pick<DanceEvent, 'startDate' | 'endDate' | 'recurrences' | 'recurring' | 'nextDateApproximate'>,
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

/** Whether one occurrence falls on one of the selected weekdays (Boston time).
 *  An empty selection matches everything. */
export function occurrenceMatchesDays(iso: string, days: readonly DayOfWeek[]): boolean {
  return days.length === 0 || days.includes(dayOfWeekFromIso(iso));
}

/**
 * The day-of-week filter, shared by the map and the feed: an event passes when
 * any of its occurrences inside the date window lands on a selected weekday.
 * The map used to test the weekday of `startDate` alone, so a series whose
 * next date was a Friday vanished from the map under a "Sat" filter while the
 * feed still listed its Saturday occurrence.
 */
export function matchesDay(
  event: OccurrenceSource,
  days: readonly DayOfWeek[],
  range: { fromMs: number; toMs: number },
): boolean {
  if (days.length === 0) return true;
  return occurrencesInRange(event, range.fromMs, range.toMs)
    .some(iso => occurrenceMatchesDays(iso, days));
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

/**
 * Whether a schedule row's note is one publish() has already folded into the
 * recurrence label ("1st Saturday of each month", "every other Friday"). Kept
 * deliberately small — it decides only whether to *show* the note, never what
 * the label says.
 */
function noteIsInLabel(note: string | undefined): boolean {
  const noteLower = (note ?? '').toLowerCase();
  return (
    noteLower.includes('every other')
    || noteLower.includes('alternating')
    || /(\d)(?:st|nd|rd|th)\s+\w+day/.test(noteLower)
    || /\b1st\b/.test(noteLower)
    || noteLower.includes('of each month')
    || noteLower.includes('of the month')
  );
}

/** The label publish() wrote, if any. Never inferred client-side. */
export function getRecurrenceLabel(event: DanceEvent): string | null {
  return event.recurrenceLabel || null;
}

/**
 * A one-row schedule's note, when it says something the recurrence label does
 * not already say. The popup hides a one-row schedule table (it would just
 * restate the pill and the "Next" line), which would otherwise drop notes like
 * "Lesson + social (18+)" — but not "1st Saturday of each month", which
 * publish() has already folded into the label.
 */
export function extraScheduleNote(event: DanceEvent): string | null {
  if (event.schedule?.length !== 1) return null;
  const { note } = event.schedule[0];
  if (!note?.trim()) return null;
  return noteIsInLabel(note) ? null : note;
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

/** Next occurrence on or after today (from recurrences[]). */
export function nextOccurrenceIso(event: DanceEvent): string | null {
  const fromMs = bostonStartOfDay(Date.now());
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

  const schedule = event.schedule;
  if (schedule && schedule.length > 0) {
    const allMapToSchedule = dates.every(iso => {
      const day = dayOfWeekFromIso(iso);
      return schedule.some(s => s.dayOfWeek === day);
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
