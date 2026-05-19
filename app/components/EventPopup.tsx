'use client';

import { useEffect, useCallback, useState, type ReactNode } from 'react';
import type { DanceEvent } from '@/types/event';
import { STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';

const URL_RE = /(https?:\/\/[^\s,)]+)/g;

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

function toIcsDate(iso: string): string {
  return new Date(iso).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
}

function generateIcsBlob(event: DanceEvent): Blob {
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Boston Latin Dance//EN',
    'BEGIN:VEVENT',
    `DTSTART:${toIcsDate(event.startDate)}`,
    `DTEND:${toIcsDate(event.endDate)}`,
    `SUMMARY:${event.name.replace(/,/g, '\\,')}`,
    `LOCATION:${event.location.replace(/,/g, '\\,')}`,
    `DESCRIPTION:${event.description.slice(0, 500).replace(/\n/g, '\\n').replace(/,/g, '\\,')}`,
    ...(event.url ? [`URL:${event.url}`] : []),
    `UID:${event.id}`,
    'END:VEVENT',
    'END:VCALENDAR',
  ];
  return new Blob([lines.join('\r\n')], { type: 'text/calendar;charset=utf-8' });
}

export default function EventPopup({ event, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const handleAddToCalendar = useCallback(() => {
    const blob = generateIcsBlob(event);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${event.name.replace(/[^a-zA-Z0-9 ]/g, '').trim().replace(/\s+/g, '-').slice(0, 50)}.ics`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [event]);

  const [descExpanded, setDescExpanded] = useState(false);

  const allDescLines = event.description.split('\n').filter(l => l.trim());
  const COLLAPSE_LIMIT = 8;
  const isLong = allDescLines.length > COLLAPSE_LIMIT;
  const descriptionLines = descExpanded ? allDescLines : allDescLines.slice(0, COLLAPSE_LIMIT);

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
        <button
          onClick={onClose}
          aria-label="Close"
          className="pretty-pill pretty-pill-neutral"
          style={{
            position: 'absolute',
            top: '0.5rem',
            right: '0.5rem',
            padding: '0.2rem 0.5rem',
            lineHeight: 1,
            zIndex: 1,
          }}
        >
          &#x2715;
        </button>

        <h2 className="text-lg font-semibold pr-8" style={{ margin: 0 }}>
          {event.name}
        </h2>

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
        {descriptionLines.length > 0 && (
          <div className="text-sm text-gray-600 leading-relaxed whitespace-pre-line border-t border-gray-100 pt-2 mt-1">
            {linkifyText(descriptionLines.join('\n'))}
            {isLong && !descExpanded && (
              <button
                onClick={() => setDescExpanded(true)}
                className="block mt-1 text-xs font-medium text-rose-500 hover:text-rose-700 cursor-pointer"
              >
                Show more ({allDescLines.length - COLLAPSE_LIMIT} more lines)
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
          <button
            onClick={handleAddToCalendar}
            className="pretty-pill pretty-pill-blue"
          >
            Add to Calendar
          </button>
        </div>
      </div>
    </div>
  );
}
