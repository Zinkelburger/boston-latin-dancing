'use client';

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import allEvents from '@/data/events-published.json';
import type { DanceEvent } from '@/types/event';
import { SITE_URL, STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';
import {
  extraScheduleNote,
  formatEventTimeRange,
  getRecurrenceLabel,
  nextOccurrenceIso,
  occurrenceEndDate,
  resolveDisplayOccurrence,
  shouldShowNextOccurrence,
} from '@/lib/recurrences';
import { findActiveInstance, isSeriesInstance } from '@/lib/search';
import { displayStartIso, hasStartDate } from '@/lib/dates';
import { cleanDisplayText } from '@/lib/display-text';
import { collectEventLinks } from '@/lib/link-label';
import ShareButton from './ShareButton';
import { PastDatesTable, UpcomingDatesTable, WeeklyScheduleTable } from './EventTable';
import MetaRow from './MetaRow';

const URL_RE = /(https?:\/\/[^\s,)]+)/g;

function linkifyText(text: string): ReactNode[] {
  const parts = text.split(URL_RE);
  // split() with a capture group puts matches at odd indices. Don't re-test
  // with URL_RE here: its /g flag makes test() stateful (lastIndex carries
  // over between calls), so alternating calls misclassify URLs.
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <a
        key={i}
        href={part}
        target="_blank"
        rel="noopener noreferrer"
        className="text-rose-500 underline hover:text-rose-700 break-all"
      >
        {part}
      </a>
    ) : (
      part
    ),
  );
}

type Props = {
  event: DanceEvent;
  onClose: () => void;
  /** Navigate to a different event (used for "next instance" links). */
  onNavigate?: (event: DanceEvent) => void;
  /** Specific occurrence clicked in feed view. */
  displayDate?: string | null;
  /** Active filter window — used to pick first in-range occurrence on map. */
  fromMs?: number;
  toMs?: number;
};

function toGcalDate(iso: string): string {
  return new Date(iso)
    .toISOString()
    .replace(/[-:]/g, '')
    .replace(/\.\d{3}/, '');
}

function googleCalendarUrl(event: DanceEvent, startDate: string, endDate: string): string {
  const params = new URLSearchParams({
    action: 'TEMPLATE',
    text: event.name,
    dates: `${toGcalDate(startDate)}/${toGcalDate(endDate)}`,
    location: event.location,
    details: [event.description.slice(0, 500), event.url].filter(Boolean).join('\n\n'),
  });
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}

export default function EventPopup({
  event,
  onClose,
  onNavigate,
  displayDate,
  fromMs,
  toMs,
}: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // A modal dialog owns focus while open: land on the close button so
  // keyboard and screen-reader users start inside it, and hand focus back to
  // whatever opened it (a feed card, a search row) when it closes.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => {
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  // Search-only venue records ship without dates on purpose (irregular
  // schedule) — skip everything date-derived for them.
  const hasDates = hasStartDate(event);
  const { start: displayStart, end: displayEnd } = hasDates
    ? resolveDisplayOccurrence(event, { displayDate, fromMs, toMs })
    : { start: '', end: '' };
  const calendarUrl = hasDates ? googleCalendarUrl(event, displayStart, displayEnd) : null;
  const shareUrl = event.slug ? `${SITE_URL}/event/${event.slug}` : '';

  const [descExpanded, setDescExpanded] = useState(false);

  const cleanDesc = cleanDisplayText(event.description);
  const CHAR_LIMIT = 300;
  const isLong = cleanDesc.length > CHAR_LIMIT;
  const visibleDesc =
    descExpanded || !isLong
      ? cleanDesc
      : cleanDesc.slice(0, cleanDesc.lastIndexOf(' ', CHAR_LIMIT)) + '…';

  const allLinks = collectEventLinks(event);
  const recurrenceLabel = getRecurrenceLabel(event);
  const scheduleNote = extraScheduleNote(event);
  const nextIso = shouldShowNextOccurrence(event) ? nextOccurrenceIso(event) : null;

  // The live listing this archived event has become, for the "Next up" link.
  // Dated candidates win over dateless venue records, so the link can always
  // name a date.
  const nextInstance = useMemo(() => {
    const next = findActiveInstance(event, allEvents as DanceEvent[]);
    return next && hasStartDate(next) ? next : null;
  }, [event]);

  // Past instances of this series (archived events with matching names) —
  // the history record shown on archived and search-only events.
  const pastInstances = useMemo(() => {
    if (!event.archived && !event.searchOnly) return [];
    return (allEvents as DanceEvent[])
      .filter(e => e.archived && e.id !== event.id && e.startDate)
      .filter(e => isSeriesInstance(event, e))
      .sort((a, b) => new Date(b.startDate).getTime() - new Date(a.startDate).getTime())
      .slice(0, 8);
  }, [event]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={event.name}
      className="event-popup-backdrop"
      onClick={onClose}
    >
      <div className="event-popup-card" onClick={e => e.stopPropagation()}>
        <div className="event-popup-grabber" aria-hidden="true" />
        <div className="event-popup-header">
          <div className="event-popup-heading">
            <div className="event-popup-actions">
              {shareUrl && (
                <ShareButton
                  url={shareUrl}
                  title={event.name}
                  text={cleanDesc.slice(0, 120) || undefined}
                  className="shrink-0 text-xs"
                />
              )}
              <button
                ref={closeRef}
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="pretty-pill pretty-pill-neutral shrink-0 text-xs event-popup-close"
              >
                &#x2715;
              </button>
            </div>
            <h2 className="event-title">{event.name}</h2>
          </div>

          <div className="flex flex-wrap gap-1.5">
            {event.styles.map(style => (
              <span key={style} className={`pretty-pill ${STYLE_PILL_CLASS[style]} text-xs`}>
                {STYLE_LABELS[style]}
              </span>
            ))}
            {event.special && (
              <span className="pretty-pill pretty-pill-fuchsia text-xs">Big Event</span>
            )}
            {event.recurring && !recurrenceLabel && (
              <span className="pretty-pill pretty-pill-neutral text-xs">Recurring</span>
            )}
            {recurrenceLabel && (event.schedule?.length ?? 0) <= 1 && (
              <span className="pretty-pill pretty-pill-sky text-xs">{recurrenceLabel}</span>
            )}
            {event.nextDateApproximate && !(event.searchOnly && !event.archived) && (
              <span className="pretty-pill pretty-pill-amber text-xs">Date unconfirmed</span>
            )}
          </div>

          <div className="event-popup-facts">
            {hasDates && !(event.schedule && event.schedule.length > 0) && (
              <MetaRow icon="calendar">
                {formatEventTimeRange(displayStart, displayEnd)}
                {event.archived && ' — this event has passed'}
              </MetaRow>
            )}

            {!hasDates && <MetaRow icon="calendar">No confirmed date yet</MetaRow>}

            {event.archived && nextInstance && (
              <div className="text-sm text-gray-600">
                Next up:{' '}
                <button
                  onClick={() => onNavigate?.(nextInstance)}
                  className="underline font-medium hover:text-gray-900 cursor-pointer"
                >
                  {nextInstance.name} —{' '}
                  {formatEventTimeRange(
                    displayStartIso(nextInstance),
                    occurrenceEndDate(nextInstance, displayStartIso(nextInstance)),
                  )}
                </button>
              </div>
            )}

            {nextIso && event.schedule && event.schedule.length > 0 && (
              <MetaRow icon="calendar">
                Next: {formatEventTimeRange(nextIso, occurrenceEndDate(event, nextIso))}
              </MetaRow>
            )}

            {event.location && <MetaRow icon="pin">{event.location}</MetaRow>}

            {event.cost && <MetaRow icon="cost">{event.cost}</MetaRow>}

            {scheduleNote && <MetaRow icon="info">{scheduleNote}</MetaRow>}
          </div>
        </div>

        {/* Story + extra tables scroll; identity and logistics stay put */}
        <div className="event-popup-body">
          {cleanDesc.trim() && (
            <div className="event-popup-desc">
              {linkifyText(visibleDesc)}
              {isLong && !descExpanded && (
                <button
                  type="button"
                  onClick={() => setDescExpanded(true)}
                  className="block mt-1 text-xs font-medium text-rose-500 hover:text-rose-700 cursor-pointer"
                >
                  Show more
                </button>
              )}
              {isLong && descExpanded && (
                <button
                  type="button"
                  onClick={() => setDescExpanded(false)}
                  className="block mt-1 text-xs font-medium text-rose-500 hover:text-rose-700 cursor-pointer"
                >
                  Show less
                </button>
              )}
            </div>
          )}

          <UpcomingDatesTable event={event} className="mt-1" />

          {/* A one-row table would just restate the pill and the "Next" line, so
            a single weekly slot renders as neither. Its note travels with the
            facts strip above. */}
          {event.schedule && event.schedule.length > 1 && (
            <WeeklyScheduleTable schedule={event.schedule} className="mt-1" />
          )}

          {pastInstances.length > 0 && (
            <PastDatesTable current={event} pastInstances={pastInstances} className="mt-1" />
          )}
        </div>

        {/* Links — pinned footer, always visible */}
        <div className="event-popup-footer">
          {allLinks.map((lnk, i) => (
            <a
              key={i}
              href={lnk.url}
              target="_blank"
              rel="noopener"
              className="pretty-pill pretty-pill-rose"
            >
              {lnk.icon} {lnk.label}
            </a>
          ))}
          {event.lat != null && event.lng != null && (
            <a
              href={`https://www.google.com/maps/search/?api=1&query=${event.lat},${event.lng}`}
              target="_blank"
              rel="noopener"
              className="pretty-pill pretty-pill-emerald"
            >
              Google Maps
            </a>
          )}
          {calendarUrl && (
            <a
              href={calendarUrl}
              target="_blank"
              rel="noopener"
              className="pretty-pill pretty-pill-blue"
            >
              Add to Calendar
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
