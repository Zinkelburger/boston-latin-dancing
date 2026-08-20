'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
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
import { isSeriesInstance, normalizeEventName } from '@/lib/search';
import { stripHtml } from '@/lib/strip-html';
import { collectEventLinks } from '@/lib/link-label';
import ShareButton from './ShareButton';
import { PastDatesTable, UpcomingDatesTable, WeeklyScheduleTable } from './EventTable';

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
  return new Date(iso).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
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


export default function EventPopup({ event, onClose, onNavigate, displayDate, fromMs, toMs }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Search-only venue records ship without dates on purpose (irregular
  // schedule) — skip everything date-derived for them.
  const hasDates = Boolean(event.startDate);
  const { start: displayStart, end: displayEnd } = hasDates
    ? resolveDisplayOccurrence(event, { displayDate, fromMs, toMs })
    : { start: '', end: '' };
  const calendarUrl = hasDates ? googleCalendarUrl(event, displayStart, displayEnd) : null;
  const shareUrl = event.slug ? `${SITE_URL}/event/${event.slug}` : '';

  const [descExpanded, setDescExpanded] = useState(false);

  const cleanDesc = stripHtml(event.description);
  const CHAR_LIMIT = 300;
  const isLong = cleanDesc.length > CHAR_LIMIT;
  const visibleDesc = descExpanded || !isLong
    ? cleanDesc
    : cleanDesc.slice(0, cleanDesc.lastIndexOf(' ', CHAR_LIMIT)) + '…';

  const allLinks = collectEventLinks(event);
  const recurrenceLabel = getRecurrenceLabel(event);
  const scheduleNote = extraScheduleNote(event);
  const nextIso = shouldShowNextOccurrence(event) ? nextOccurrenceIso(event) : null;

  const nextInstance = useMemo(() => {
    if (!event.archived) return null;
    const norm = normalizeEventName(event.name);
    const active = (allEvents as DanceEvent[]).filter(e => !e.archived);

    // Only match on exact (or near-exact) name, optionally requiring same venue
    // for fuzzy matches. Common dance words like "salsa", "bachata", "social"
    // cause too many false positives with loose word-overlap matching.
    const DANCE_STOPWORDS = new Set([
      'salsa', 'bachata', 'kizomba', 'zouk', 'merengue', 'latin',
      'social', 'dance', 'dancing', 'night', 'party', 'boston',
      'class', 'workshop', 'lesson', 'free',
    ]);
    const normWords = new Set(norm.split(' ').filter(w => w.length > 2 && !DANCE_STOPWORDS.has(w)));

    for (const candidate of active) {
      const cn = normalizeEventName(candidate.name);
      if (cn === norm) return candidate;
      if (cn.includes(norm) || norm.includes(cn)) {
        const venueA = (event.location || '').split(',')[0].toLowerCase().trim();
        const venueB = (candidate.location || '').split(',')[0].toLowerCase().trim();
        if (venueA && venueB && venueA === venueB) return candidate;
      }
    }

    // Fuzzy: require same venue + significant non-generic word overlap
    if (normWords.size >= 2) {
      for (const candidate of active) {
        const cn = normalizeEventName(candidate.name);
        const cWords = new Set(cn.split(' ').filter(w => w.length > 2 && !DANCE_STOPWORDS.has(w)));
        const overlap = [...normWords].filter(w => cWords.has(w)).length;
        const smaller = Math.min(normWords.size, cWords.size);
        if (smaller >= 2 && overlap >= smaller * 0.7) {
          const venueA = (event.location || '').split(',')[0].toLowerCase().trim();
          const venueB = (candidate.location || '').split(',')[0].toLowerCase().trim();
          if (venueA && venueB && venueA === venueB) return candidate;
        }
      }
    }
    return null;
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
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(2, 6, 23, 0.45)',
      }}
      onClick={onClose}
    >
      <div
        className="relative"
        style={{
          width: 'min(92vw, 420px)',
          maxHeight: '84vh',
          background: '#ffffff',
          borderRadius: '1rem',
          boxShadow: '0 18px 50px rgba(0,0,0,0.28)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Scrollable body — the links footer below stays pinned */}
        <div
          style={{
            overflowY: 'auto',
            padding: '1.25rem 1.25rem 0.75rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.5rem',
          }}
        >
        <div className="flex items-start">
          <h2 className="text-lg font-semibold min-w-0 flex-1" style={{ margin: 0 }}>
            {event.name}
          </h2>
          <div className="flex items-center shrink-0" style={{ gap: '0.5rem' }}>
            {shareUrl && (
              <ShareButton url={shareUrl} title={event.name} text={stripHtml(event.description).slice(0, 120) || undefined} className="shrink-0 text-xs" />
            )}
            <button
              onClick={onClose}
              aria-label="Close"
              className="pretty-pill pretty-pill-neutral shrink-0"
              style={{ padding: '0.2rem 0.5rem', lineHeight: 1 }}
            >
              &#x2715;
            </button>
          </div>
        </div>

        {/* Style pills */}
        <div className="flex flex-wrap gap-1.5">
          {event.special && (
            <span className="pretty-pill pretty-pill-amber text-xs">Big Event</span>
          )}
          {event.styles.map(style => (
            <span key={style} className={`pretty-pill ${STYLE_PILL_CLASS[style]} text-xs`}>
              {STYLE_LABELS[style]}
            </span>
          ))}
          {event.recurring && !recurrenceLabel && (
            <span className="pretty-pill pretty-pill-neutral text-xs">Recurring</span>
          )}
        </div>

        {recurrenceLabel && (
          <div className="flex flex-wrap gap-1.5 items-center">
            <span className="pretty-pill pretty-pill-sky text-xs">
              {recurrenceLabel}
            </span>
            {/* The search-only date line already says the date is unconfirmed */}
            {event.nextDateApproximate && !(event.searchOnly && !event.archived) && (
              <span className="pretty-pill pretty-pill-amber text-xs">
                Date unconfirmed
              </span>
            )}
          </div>
        )}

        {hasDates && !(event.schedule && event.schedule.length > 0) && (
          <div className="text-sm text-gray-600">
            📅 {formatEventTimeRange(displayStart, displayEnd)}
            {event.archived && ' — this event has passed'}
          </div>
        )}

        {/* Search-only records have no date — plain note in the date's place */}
        {!hasDates && (
          <div className="text-sm text-gray-600">
            📅 No confirmed date yet
          </div>
        )}

        {event.archived && nextInstance && (
          <div className="text-sm text-gray-600">
            Next up:{' '}
            <button
              onClick={() => onNavigate?.(nextInstance)}
              className="underline font-medium hover:text-gray-900 cursor-pointer"
            >
              {nextInstance.name} — {formatEventTimeRange(nextInstance.startDate, nextInstance.endDate)}
            </button>
          </div>
        )}

        {nextIso && event.schedule && event.schedule.length > 0 && (
          <div className="text-sm text-gray-600">
            📅 Next: {formatEventTimeRange(nextIso, occurrenceEndDate(event, nextIso))}
          </div>
        )}

        <UpcomingDatesTable event={event} className="mt-1" />

        {/* One row would just restate the pill and the "Next" line as a table,
            but its note can still carry something neither of those says. */}
        {event.schedule && event.schedule.length > 1 && (
          <WeeklyScheduleTable schedule={event.schedule} className="mt-1" />
        )}
        {scheduleNote && (
          <div className="text-sm text-gray-600">ℹ️ {scheduleNote}</div>
        )}

        {/* Location */}
        {event.location && (
          <div className="text-sm text-gray-600">
            📍 {event.location}
          </div>
        )}

        {/* Cost */}
        {event.cost && (
          <div className="text-sm font-medium text-rose-600">
            💵 {event.cost}
          </div>
        )}

        {/* Description */}
        {cleanDesc.trim() && (
          <div className="text-sm text-gray-600 leading-relaxed whitespace-pre-line border-t border-gray-100 pt-2 mt-1">
            {linkifyText(visibleDesc)}
            {isLong && !descExpanded && (
              <button
                onClick={() => setDescExpanded(true)}
                className="block mt-1 text-xs font-medium text-rose-500 hover:text-rose-700 cursor-pointer"
              >
                Show more
              </button>
            )}
            {isLong && descExpanded && (
              <button
                onClick={() => setDescExpanded(false)}
                className="block mt-1 text-xs font-medium text-rose-500 hover:text-rose-700 cursor-pointer"
              >
                Show less
              </button>
            )}
          </div>
        )}

        {pastInstances.length > 0 && (
          <PastDatesTable current={event} pastInstances={pastInstances} className="mt-1" />
        )}
        </div>

        {/* Links — pinned footer, always visible */}
        <div
          className="flex flex-wrap gap-2"
          style={{ padding: '0.75rem 1.25rem', borderTop: '1px solid #f3f4f6', flexShrink: 0 }}
        >
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
          {event.lat && event.lng && (
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
