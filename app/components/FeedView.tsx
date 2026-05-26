'use client';

import { useMemo } from 'react';
import type { DanceEvent, DayOfWeek } from '@/types/event';
import { STYLE_LABELS, STYLE_PILL_CLASS, SITE_URL } from '@/lib/constants';
import { stripHtml } from '@/lib/strip-html';
import { getRecurrenceLabel, recurrencesInRange, isDateOnlyEvent } from '@/lib/recurrences';
import ShareButton from './ShareButton';

const DAY_NAMES: DayOfWeek[] = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

type FeedEntry = {
  event: DanceEvent;
  displayDate: string;
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  });
}

function dateKey(iso: string): string {
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

type DateGroup = { key: string; label: string; entries: FeedEntry[] };

function expandAndGroup(
  events: DanceEvent[],
  selectedDays: DayOfWeek[],
  fromMs: number,
  toMs: number,
): DateGroup[] {
  const entries: FeedEntry[] = [];

  for (const event of events) {
    if (event.recurrences && event.recurrences.length > 0) {
      for (const recDate of recurrencesInRange(event.recurrences, fromMs, toMs)) {
        const d = new Date(recDate);
        const day = DAY_NAMES[d.getDay()];
        if (selectedDays.length > 0 && !selectedDays.includes(day)) continue;
        entries.push({ event, displayDate: recDate });
      }
    } else {
      const d = new Date(event.startDate);
      const day = DAY_NAMES[d.getDay()];
      if (selectedDays.length > 0 && !selectedDays.includes(day)) continue;
      entries.push({ event, displayDate: event.startDate });
    }
  }

  entries.sort(
    (a, b) => new Date(a.displayDate).getTime() - new Date(b.displayDate).getTime(),
  );

  const groups = new Map<string, FeedEntry[]>();
  for (const entry of entries) {
    const key = dateKey(entry.displayDate);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(entry);
  }

  return [...groups.entries()].map(([key, evts]) => ({
    key,
    label: formatDate(evts[0].displayDate),
    entries: evts,
  }));
}

type Props = {
  events: DanceEvent[];
  selectedDays: DayOfWeek[];
  fromMs: number;
  toMs: number;
  onSelectEvent: (event: DanceEvent, displayDate?: string) => void;
};

export default function FeedView({ events, selectedDays, fromMs, toMs, onSelectEvent }: Props) {
  const grouped = useMemo(
    () => expandAndGroup(events, selectedDays, fromMs, toMs),
    [events, selectedDays, fromMs, toMs],
  );
  const totalEntries = useMemo(() => grouped.reduce((n, g) => n + g.entries.length, 0), [grouped]);

  return (
    <div className="feed-view">
      <div className="feed-header">
        <span className="feed-count">{totalEntries} events</span>
      </div>

      <div className="feed-scroll">
        {grouped.map(group => (
          <div key={group.key} className="feed-group">
            <div className="feed-group-label">{group.label}</div>
            {group.entries.map(entry => (
              <FeedCard
                key={`${entry.event.id}-${entry.displayDate}`}
                event={entry.event}
                displayDate={entry.displayDate}
                onSelect={() => onSelectEvent(entry.event, entry.displayDate)}
              />
            ))}
          </div>
        ))}

        {grouped.length === 0 && (
          <div className="feed-empty">
            No events match your filters.
          </div>
        )}
      </div>
    </div>
  );
}

function scheduleTimeForDate(event: DanceEvent, displayDate: string): string | null {
  const d = new Date(displayDate);
  const day = DAY_NAMES[d.getDay()];
  const entry = event.schedule?.find(s => s.dayOfWeek === day);
  return entry?.time ?? null;
}

function FeedCard({
  event,
  displayDate,
  onSelect,
}: {
  event: DanceEvent;
  displayDate: string;
  onSelect: () => void;
}) {
  const cleanDesc = stripHtml(event.description);
  const shortDesc =
    cleanDesc.length > 120
      ? cleanDesc.slice(0, cleanDesc.lastIndexOf(' ', 120)) + '...'
      : cleanDesc;

  const shareUrl = event.slug ? `${SITE_URL}/event/${event.slug}` : '';
  const scheduleTime = event.recurrences ? scheduleTimeForDate(event, displayDate) : null;
  const recurrenceLabel = getRecurrenceLabel(event);

  return (
    <div role="button" tabIndex={0} className="feed-card" onClick={onSelect} onKeyDown={e => { if (e.key === 'Enter') onSelect(); }}>
      <div className="feed-card-top">
        <div className="feed-card-pills">
          {event.styles.map(style => (
            <span
              key={style}
              className={`pretty-pill ${STYLE_PILL_CLASS[style]} text-xs`}
            >
              {STYLE_LABELS[style]}
            </span>
          ))}
          {event.recurring && !recurrenceLabel && (
            <span className="pretty-pill pretty-pill-neutral text-xs">
              Recurring
            </span>
          )}
        </div>
        {shareUrl && (
          <div onClick={e => e.stopPropagation()}>
            <ShareButton
              url={shareUrl}
              title={event.name}
              className="shrink-0 text-xs"
            />
          </div>
        )}
      </div>

      <h3 className="feed-card-title">{event.name}</h3>

      {recurrenceLabel && (
        <div className="text-sm font-medium text-gray-700">{recurrenceLabel}</div>
      )}

      <div className="feed-card-meta">
        <span className="feed-card-date">
          {scheduleTime
            ? `${formatDate(displayDate)} \u00B7 ${scheduleTime}`
            : isDateOnlyEvent(event.startDate, event.endDate)
              ? formatDate(displayDate)
              : `${formatDate(displayDate)} \u00B7 ${formatTime(event.startDate)} – ${formatTime(event.endDate)}`
          }
        </span>
      </div>

      {event.location && (
        <div className="feed-card-location">{event.location}</div>
      )}

      {event.cost && <div className="feed-card-cost">{event.cost}</div>}

      {shortDesc && (
        <p className="feed-card-desc">{shortDesc}</p>
      )}
    </div>
  );
}
