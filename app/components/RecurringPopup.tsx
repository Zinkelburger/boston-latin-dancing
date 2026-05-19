'use client';

import { useEffect, useMemo } from 'react';
import type { RecurringVenue, DayOfWeek } from '@/types/event';
import { STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';

type Props = {
  venue: RecurringVenue;
  onClose: () => void;
};

const DAY_INDEX: Record<DayOfWeek, number> = {
  Sunday: 0, Monday: 1, Tuesday: 2, Wednesday: 3,
  Thursday: 4, Friday: 5, Saturday: 6,
};

const DAY_SHORT: Record<DayOfWeek, string> = {
  Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed',
  Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat', Sunday: 'Sun',
};

function getNextDatesForSchedule(schedule: { dayOfWeek: DayOfWeek }[], count: number): Date[] {
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const targets = schedule.map(s => DAY_INDEX[s.dayOfWeek]);
  const dates: Date[] = [];

  for (let offset = 0; dates.length < count && offset < 30; offset++) {
    const d = new Date(today);
    d.setDate(today.getDate() + offset);
    if (targets.includes(d.getDay())) {
      dates.push(d);
    }
  }
  return dates;
}

function googleCalendarUrl(venue: RecurringVenue, date: Date): string {
  const sched = venue.schedule.find(
    s => DAY_INDEX[s.dayOfWeek] === date.getDay()
  ) || venue.schedule[0];

  const startTime = sched.time.split('–')[0].trim();
  const endTime = sched.time.split('–')[1]?.trim() || '';

  function parseTime(timeStr: string, baseDate: Date): Date {
    const match = timeStr.match(/(\d+):(\d+)\s*(AM|PM)/i);
    if (!match) return baseDate;
    let h = parseInt(match[1]);
    const m = parseInt(match[2]);
    const pm = match[3].toUpperCase() === 'PM';
    if (pm && h !== 12) h += 12;
    if (!pm && h === 12) h = 0;
    const d = new Date(baseDate);
    d.setHours(h, m, 0, 0);
    return d;
  }

  const start = parseTime(startTime, date);
  let end = parseTime(endTime, date);
  if (end <= start) end.setDate(end.getDate() + 1);

  const fmt = (d: Date) => d.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');

  const params = new URLSearchParams({
    action: 'TEMPLATE',
    text: venue.name,
    dates: `${fmt(start)}/${fmt(end)}`,
    location: venue.location,
    details: venue.description.slice(0, 500),
  });
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}

export default function RecurringPopup({ venue, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const upcomingDates = useMemo(
    () => getNextDatesForSchedule(venue.schedule, 4),
    [venue.schedule],
  );

  const nextCalUrl = upcomingDates.length > 0
    ? googleCalendarUrl(venue, upcomingDates[0])
    : '#';

  const scheduleDays = venue.schedule.map(s => DAY_SHORT[s.dayOfWeek]).join(', ');

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={venue.name}
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
          {venue.name}
        </h2>

        {/* Style pills + cost */}
        <div className="flex flex-wrap items-center gap-1.5">
          {venue.styles.map(style => (
            <span key={style} className={`pretty-pill ${STYLE_PILL_CLASS[style]} text-xs`}>
              {STYLE_LABELS[style]}
            </span>
          ))}
          {venue.cost && (
            <span className="pretty-pill pretty-pill-neutral text-xs font-medium">
              {venue.cost}
            </span>
          )}
        </div>

        {/* Location */}
        <div className="text-sm text-gray-500">
          {venue.location}
        </div>

        {/* Schedule table */}
        <table className="w-full text-sm border-collapse mt-1">
          <tbody>
            {venue.schedule.map(s => (
              <tr key={s.dayOfWeek} className="border-t border-gray-100">
                <td className="py-1.5 pr-3 font-semibold text-gray-700 whitespace-nowrap w-[1%]">
                  {DAY_SHORT[s.dayOfWeek]}
                </td>
                <td className="py-1.5 pr-3 text-gray-600 whitespace-nowrap">
                  {s.time}
                </td>
                <td className="py-1.5 text-gray-400 text-xs">
                  {s.note}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* Upcoming dates */}
        <div className="mt-1">
          <div className="text-xs font-semibold uppercase text-gray-400 mb-1.5">Next up</div>
          <div className="flex flex-wrap gap-1.5">
            {upcomingDates.map(d => (
              <span key={d.toISOString()} className="pretty-pill pretty-pill-ghost text-xs">
                {d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
              </span>
            ))}
          </div>
        </div>

        {/* Description */}
        <div className="text-sm text-gray-500 leading-relaxed whitespace-pre-line border-t border-gray-100 pt-2 mt-2">
          {venue.description}
        </div>

        {/* Links */}
        <div className="flex flex-wrap gap-2 mt-1">
          {venue.url && (
            <a
              href={venue.url}
              target="_blank"
              rel="noopener"
              className="pretty-pill pretty-pill-rose"
            >
              Website
            </a>
          )}
          <a
            href={`https://www.google.com/maps/search/?api=1&query=${venue.lat},${venue.lng}`}
            target="_blank"
            rel="noopener"
            className="pretty-pill pretty-pill-emerald"
          >
            Google Maps
          </a>
          <a
            href={nextCalUrl}
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
