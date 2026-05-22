'use client';

import { useEffect, useCallback, useState, type ReactNode } from 'react';
import type { DanceEvent, DayOfWeek } from '@/types/event';
import { SITE_URL, STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';
import { stripHtml } from '@/lib/strip-html';
import ShareButton from './ShareButton';

const URL_RE = /(https?:\/\/[^\s,)]+)/g;

const DAY_SHORT: Record<DayOfWeek, string> = {
  Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed',
  Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat', Sunday: 'Sun',
};

function linkifyText(text: string): ReactNode[] {
  const parts = text.split(URL_RE);
  return parts.map((part, i) =>
    URL_RE.test(part) ? (
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
};

function formatTimeRange(start: string, end: string): string {
  const s = new Date(start);
  const e = new Date(end);
  const sameDay = s.toDateString() === e.toDateString();

  const dateStr = s.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });
  const startTime = s.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  const endTime = e.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

  if (sameDay) {
    return `${dateStr}, ${startTime} – ${endTime}`;
  }
  const endDateStr = e.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });
  return `${dateStr} ${startTime} – ${endDateStr} ${endTime}`;
}

function linkLabel(url: string): { label: string; icon: string } {
  try {
    const host = new URL(url).hostname.replace(/^www\./, '');
    if (host.includes('eventbrite.com')) return { label: 'Eventbrite', icon: '🎟' };
    if (host.includes('facebook.com')) return { label: 'Facebook', icon: '📘' };
    if (host.includes('instagram.com')) return { label: 'Instagram', icon: '📷' };
    if (host.includes('tickeri.com')) return { label: 'Tickeri', icon: '🎫' };
    if (host.includes('humanitix.com')) return { label: 'Humanitix', icon: '🎟' };
    if (host.includes('resy.com')) return { label: 'Resy', icon: '🍽' };
    if (host.includes('danceplace.com')) return { label: 'DancePlace', icon: '💃' };
    if (host.includes('metamovements.com')) return { label: 'MetaMovements', icon: '🌀' };
    const short = host.length > 20 ? host.slice(0, 18) + '...' : host;
    return { label: short, icon: '🔗' };
  } catch {
    return { label: 'Event Link', icon: '🔗' };
  }
}

function toGcalDate(iso: string): string {
  return new Date(iso).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
}

function googleCalendarUrl(event: DanceEvent): string {
  const params = new URLSearchParams({
    action: 'TEMPLATE',
    text: event.name,
    dates: `${toGcalDate(event.startDate)}/${toGcalDate(event.endDate)}`,
    location: event.location,
    details: [event.description.slice(0, 500), event.url].filter(Boolean).join('\n\n'),
  });
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}

export default function EventPopup({ event, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const calendarUrl = googleCalendarUrl(event);
  const shareUrl = event.slug ? `${SITE_URL}/event/${event.slug}` : '';

  const [descExpanded, setDescExpanded] = useState(false);

  const cleanDesc = stripHtml(event.description);
  const CHAR_LIMIT = 300;
  const isLong = cleanDesc.length > CHAR_LIMIT;
  const visibleDesc = descExpanded || !isLong
    ? cleanDesc
    : cleanDesc.slice(0, cleanDesc.lastIndexOf(' ', CHAR_LIMIT)) + '…';

  const link = event.url ? linkLabel(event.url) : null;

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
          overflowY: 'auto',
          background: '#ffffff',
          borderRadius: '1rem',
          padding: '1.25rem',
          boxShadow: '0 18px 50px rgba(0,0,0,0.28)',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.5rem',
        }}
        onClick={e => e.stopPropagation()}
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
          {event.styles.map(style => (
            <span key={style} className={`pretty-pill ${STYLE_PILL_CLASS[style]} text-xs`}>
              {STYLE_LABELS[style]}
            </span>
          ))}
          {event.recurring && (
            <span className="pretty-pill pretty-pill-neutral text-xs">Recurring</span>
          )}
        </div>

        {/* Time */}
        <div className="text-sm text-gray-600">
          {formatTimeRange(event.startDate, event.endDate)}
        </div>

        {/* Upcoming dates for recurring series */}
        {event.recurrences && event.recurrences.length > 1 && (
          <div className="text-sm text-gray-500">
            <span className="font-medium text-gray-600">Upcoming dates: </span>
            {event.recurrences.map(d => new Date(d)).map((d, i) => (
              <span key={i}>
                {i > 0 && <span className="text-gray-300 mx-0.5">&middot;</span>}
                <span className={d.getTime() >= Date.now() - 86400000 ? 'text-gray-700' : 'text-gray-400 line-through'}>
                  {d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </span>
              </span>
            ))}
          </div>
        )}

        {/* Weekly schedule table (for venue-style recurring events) */}
        {event.schedule && event.schedule.length > 0 && (
          <table className="w-full text-sm border-collapse mt-1">
            <tbody>
              {event.schedule.map(s => (
                <tr key={s.dayOfWeek} className="border-t border-gray-100">
                  <td className="py-1.5 pr-3 font-semibold text-gray-700 whitespace-nowrap w-[1%]">
                    {DAY_SHORT[s.dayOfWeek]}
                  </td>
                  <td className="py-1.5 pr-3 text-gray-600 whitespace-nowrap">{s.time}</td>
                  <td className="py-1.5 text-gray-400 text-xs">{s.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {/* Location */}
        {event.location && (
          <div className="text-sm text-gray-500">
            {event.location}
          </div>
        )}

        {/* Cost */}
        {event.cost && (
          <div className="text-sm font-medium text-rose-600">
            {event.cost}
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

        {/* Links */}
        <div className="flex flex-wrap gap-2 mt-1">
          {link && event.url && (
            <a
              href={event.url}
              target="_blank"
              rel="noopener"
              className="pretty-pill pretty-pill-rose"
            >
              {link.icon} {link.label}
            </a>
          )}
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
          <a
            href={calendarUrl}
            target="_blank"
            rel="noopener"
            className="pretty-pill pretty-pill-blue"
          >
            Add to Calendar
          </a>
        </div>
      </div>
    </div>
  );
}
