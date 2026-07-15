import type { ReactNode } from 'react';
import type { DanceEvent, DayOfWeek, RecurringSchedule } from '@/types/event';
import {
  formatRecurrenceDate,
  recurrenceTimeRange,
  shouldShowUpcomingDates,
  upcomingRecurrences,
} from '@/lib/recurrences';
import { stripHtml } from '@/lib/strip-html';

const DAY_SHORT: Record<DayOfWeek, string> = {
  Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed',
  Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat', Sunday: 'Sun',
};

type TableProps = {
  title?: string;
  className?: string;
};

function EventTable({
  title,
  className = '',
  children,
}: TableProps & { children: ReactNode }) {
  return (
    <div className={`event-table-wrap ${className}`.trim()}>
      {title && (
        <div className="event-table-title">{title}</div>
      )}
      <table className="event-table">
        {children}
      </table>
    </div>
  );
}

export function WeeklyScheduleTable({
  schedule,
  title = 'Weekly schedule',
  className,
}: {
  schedule: RecurringSchedule[];
  title?: string;
  className?: string;
}) {
  if (schedule.length === 0) return null;

  return (
    <EventTable title={title} className={className}>
      <thead>
        <tr>
          <th>Day</th>
          <th>Time</th>
          <th>Note</th>
        </tr>
      </thead>
      <tbody>
        {schedule.map(s => (
          <tr key={s.dayOfWeek}>
            <td className="event-table-day">{DAY_SHORT[s.dayOfWeek]}</td>
            <td className="event-table-time">{s.time}</td>
            <td className="event-table-note">{s.note}</td>
          </tr>
        ))}
      </tbody>
    </EventTable>
  );
}

export function UpcomingDatesTable({
  event,
  title = 'Upcoming dates',
  className,
}: {
  event: DanceEvent;
  title?: string;
  className?: string;
}) {
  if (!shouldShowUpcomingDates(event)) return null;

  const dates = upcomingRecurrences(event.recurrences ?? []);
  if (dates.length === 0) return null;

  return (
    <EventTable title={title} className={className}>
      <thead>
        <tr>
          <th>Date</th>
          <th>Time</th>
        </tr>
      </thead>
      <tbody>
        {dates.map(iso => (
          <tr key={iso}>
            <td className="event-table-date">{formatRecurrenceDate(iso)}</td>
            <td className="event-table-time">{recurrenceTimeRange(event, iso)}</td>
          </tr>
        ))}
      </tbody>
    </EventTable>
  );
}

export function PastDatesTable({
  current,
  pastInstances,
  title = 'Past dates',
  className,
}: {
  current: DanceEvent;
  pastInstances: DanceEvent[];
  title?: string;
  className?: string;
}) {
  if (pastInstances.length === 0) return null;

  // Only show a name column when the instances aren't all the same series name
  // (e.g. themed editions under one venue record).
  const showNames = pastInstances.some(e => e.name !== current.name);

  return (
    <EventTable title={title} className={className}>
      <thead>
        <tr>
          <th>Date</th>
          {showNames && <th>Event</th>}
        </tr>
      </thead>
      <tbody>
        {pastInstances.map(e => (
          <tr key={e.id}>
            <td className="event-table-date">{formatRecurrenceDate(e.startDate)}</td>
            {showNames && <td className="event-table-note">{e.name}</td>}
          </tr>
        ))}
      </tbody>
    </EventTable>
  );
}

export function CompactScheduleTable({
  schedule,
  className,
}: {
  schedule: RecurringSchedule[];
  className?: string;
}) {
  if (schedule.length === 0) return null;

  return (
    <EventTable className={className}>
      <tbody>
        {schedule.map(s => (
          <tr key={s.dayOfWeek}>
            <td className="event-table-day">{DAY_SHORT[s.dayOfWeek]}</td>
            <td className="event-table-time">{s.time}</td>
            <td className="event-table-note">{s.note}</td>
          </tr>
        ))}
      </tbody>
    </EventTable>
  );
}

function highlightText(text: string, tokens: string[]): ReactNode {
  if (tokens.length === 0) return text;
  const lower = text.toLowerCase();
  const marks: boolean[] = new Array(text.length).fill(false);
  for (const tok of tokens) {
    let start = 0;
    while (true) {
      const idx = lower.indexOf(tok, start);
      if (idx === -1) break;
      for (let i = idx; i < idx + tok.length; i++) marks[i] = true;
      start = idx + 1;
    }
  }
  if (!marks.some(Boolean)) return text;
  const parts: ReactNode[] = [];
  let i = 0;
  while (i < text.length) {
    const marked = marks[i];
    let j = i;
    while (j < text.length && marks[j] === marked) j++;
    const slice = text.slice(i, j);
    parts.push(marked ? <mark key={i} className="feed-highlight">{slice}</mark> : slice);
    i = j;
  }
  return <>{parts}</>;
}

function excerptAround(text: string, tokens: string[], maxLen = 80): string {
  if (text.length <= maxLen) return text;
  const lower = text.toLowerCase();
  let earliest = -1;
  for (const tok of tokens) {
    const idx = lower.indexOf(tok);
    if (idx !== -1 && (earliest === -1 || idx < earliest)) earliest = idx;
  }
  if (earliest === -1 || earliest <= maxLen / 2) {
    const end = text.lastIndexOf(' ', maxLen);
    return text.slice(0, end > 0 ? end : maxLen) + '…';
  }
  const start = Math.max(0, earliest - Math.floor(maxLen / 3));
  const wordStart = start === 0 ? 0 : text.indexOf(' ', start) + 1;
  const end = Math.min(text.length, wordStart + maxLen);
  const wordEnd = end >= text.length ? text.length : text.lastIndexOf(' ', end);
  const slice = text.slice(wordStart, wordEnd > wordStart ? wordEnd : end);
  return (wordStart > 0 ? '…' : '') + slice + (wordEnd < text.length ? '…' : '');
}

export function SearchResultsTable({
  events,
  onSelect,
  searchTokens = [],
}: {
  events: DanceEvent[];
  onSelect: (event: DanceEvent) => void;
  searchTokens?: string[];
}) {
  if (events.length === 0) return null;

  return (
    <div className="search-results-table">
      <table className="event-table">
        <thead>
          <tr>
            <th>Event</th>
            <th>When</th>
          </tr>
        </thead>
        <tbody>
          {events.map(event => {
            // Search-only venue records have no confirmed date; archived
            // results are labeled as past so they don't read as upcoming.
            const dateLabel = !event.startDate
              ? 'Varies'
              : new Date(event.startDate).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'America/New_York' });
            const desc = stripHtml(event.description);
            const snippet = searchTokens.length > 0 ? excerptAround(desc, searchTokens) : '';
            return (
              <tr
                key={event.id}
                tabIndex={0}
                role="button"
                className="search-results-row"
                onClick={() => onSelect(event)}
                onKeyDown={e => { if (e.key === 'Enter') onSelect(event); }}
              >
                <td>
                  <div className="search-results-name">{highlightText(event.name, searchTokens)}</div>
                  <div className="search-results-meta">
                    {highlightText(
                      event.styles.join(', ') + (event.location ? ` · ${event.location.split('\n')[0]}` : ''),
                      searchTokens,
                    )}
                  </div>
                  {snippet && (
                    <div className="search-results-snippet">
                      {highlightText(snippet, searchTokens)}
                    </div>
                  )}
                </td>
                <td className="search-results-when">
                  {dateLabel}
                  {event.archived && (
                    <span className="block text-[10px] uppercase tracking-wide text-gray-400">past</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
