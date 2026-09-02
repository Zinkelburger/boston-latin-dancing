import type { ReactNode } from 'react';
import type { DanceEvent, RecurringSchedule } from '@/types/event';
import {
  formatRecurrenceDate,
  recurrenceTimeRange,
  shouldShowUpcomingDates,
  upcomingRecurrences,
} from '@/lib/recurrences';
import { DAY_SHORT } from '@/lib/filter-options';
import { displayStartIso, hasStartDate } from '@/lib/dates';
import { cleanDisplayText } from '@/lib/display-text';
import { excerptAround, highlightText } from '@/lib/highlight';

type TableProps = {
  title?: string;
  className?: string;
};

function EventTable({ title, className = '', children }: TableProps & { children: ReactNode }) {
  return (
    <div className={`event-table-wrap ${className}`.trim()}>
      {title && <div className="event-table-title">{title}</div>}
      <table className="event-table">{children}</table>
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

/** Search results are one line each, so the snippet is shorter than a feed card's. */
const SNIPPET_LEN = 80;

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
            // Recurring series show their next date, not the first one.
            const dateLabel = hasStartDate(event)
              ? new Date(displayStartIso(event)).toLocaleDateString('en-US', {
                  month: 'short',
                  day: 'numeric',
                  timeZone: 'America/New_York',
                })
              : 'Varies';
            const desc = cleanDisplayText(event.description);
            const snippet =
              searchTokens.length > 0
                ? excerptAround(desc, searchTokens, { maxLen: SNIPPET_LEN })
                : '';
            return (
              <tr
                key={event.id}
                tabIndex={0}
                role="button"
                className="search-results-row"
                onClick={() => onSelect(event)}
                onKeyDown={e => {
                  if (e.key === 'Enter') onSelect(event);
                }}
              >
                <td>
                  <div className="search-results-name">
                    {highlightText(event.name, searchTokens)}
                  </div>
                  <div className="search-results-meta">
                    {highlightText(
                      event.styles.join(', ') +
                        (event.location ? ` · ${event.location.split('\n')[0]}` : ''),
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
                    <span className="block text-[10px] uppercase tracking-wide text-gray-600">
                      past
                    </span>
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
