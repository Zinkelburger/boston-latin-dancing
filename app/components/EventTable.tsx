import type { ReactNode } from 'react';
import type { DanceEvent, DayOfWeek, RecurringSchedule } from '@/types/event';
import {
  formatRecurrenceDate,
  recurrenceTimeRange,
  shouldShowUpcomingDates,
  upcomingRecurrences,
} from '@/lib/recurrences';

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

export function SearchResultsTable({
  events,
  onSelect,
}: {
  events: DanceEvent[];
  onSelect: (event: DanceEvent) => void;
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
            const d = new Date(event.startDate);
            const dateLabel = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
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
                  <div className="search-results-name">{event.name}</div>
                  <div className="search-results-meta">
                    {event.styles.join(', ')}{event.location ? ` · ${event.location.split('\n')[0]}` : ''}
                  </div>
                </td>
                <td className="search-results-when">{dateLabel}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
