import type { Metadata } from 'next';
import allEvents from '@/public/events.json';
import type { DanceEvent, DayOfWeek } from '@/types/event';
import { SITE_URL, STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';
import { stripHtml } from '@/lib/strip-html';
import EventDetailClient from './EventDetailClient';
import CollapsibleText from '@/app/components/CollapsibleText';

const events = allEvents as DanceEvent[];

type Params = { slug: string };

const DAY_SHORT: Record<DayOfWeek, string> = {
  Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed',
  Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat', Sunday: 'Sun',
};

function findBySlug(slug: string): DanceEvent | undefined {
  return events.find(e => e.slug === slug);
}

export function generateStaticParams(): Params[] {
  return events.filter(e => e.slug).map(e => ({ slug: e.slug! }));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { slug } = await params;
  const event = findBySlug(slug);
  if (!event) {
    return { title: 'Event Not Found | Boston Latin Dance Map' };
  }

  const styles = event.styles.map(s => STYLE_LABELS[s]).join(', ');
  const venue = event.location?.split('\n')[0] || '';
  const date = new Date(event.startDate).toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });

  const parts = [styles, date, venue].filter(Boolean);
  const description = parts.join(' — ');

  const url = `${SITE_URL}/event/${slug}`;

  return {
    title: `${event.name} | Boston Latin Dance Map`,
    description,
    openGraph: {
      title: event.name,
      description,
      url,
      siteName: 'Boston Latin Dance Map',
      type: 'website',
    },
    twitter: {
      card: 'summary',
      title: event.name,
      description,
    },
    alternates: {
      canonical: url,
    },
  };
}

function formatTimeRange(start: string, end: string): string {
  const s = new Date(start);
  const e = new Date(end);
  const sameDay = s.toDateString() === e.toDateString();

  const dateStr = s.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });
  const startTime = s.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  const endTime = e.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

  if (sameDay) return `${dateStr}, ${startTime} – ${endTime}`;

  const endDateStr = e.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });
  return `${dateStr} ${startTime} – ${endDateStr} ${endTime}`;
}

export default async function EventPage({ params }: { params: Promise<Params> }) {
  const { slug } = await params;
  const event = findBySlug(slug);

  if (!event) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-gray-800 mb-2">Event Not Found</h1>
          <p className="text-gray-500 mb-4">This event may have been removed or the link is incorrect.</p>
          <a href="/" className="pretty-pill pretty-pill-rose">Back to Map</a>
        </div>
      </div>
    );
  }

  const shareUrl = `${SITE_URL}/event/${slug}`;
  const shareText = `${event.name} — ${formatTimeRange(event.startDate, event.endDate)}`;

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-lg mx-auto px-4 py-8">
        <a
          href={`/#event=${slug}`}
          className="inline-flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-600 mb-6"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="19" y1="12" x2="5" y2="12" />
            <polyline points="12 19 5 12 12 5" />
          </svg>
          View on Map
        </a>

        <div style={{
          background: '#ffffff',
          borderRadius: '1rem',
          padding: '1.5rem',
          boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.625rem',
        }}>
          <div className="flex items-start gap-2">
            <h1 className="text-xl font-semibold flex-1" style={{ margin: 0 }}>
              {event.name}
            </h1>
            <EventDetailClient url={shareUrl} title={event.name} text={shareText} />
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

          {/* Weekly schedule table */}
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
            <div className="text-sm text-gray-500">{event.location}</div>
          )}

          {/* Cost */}
          {event.cost && (
            <div className="text-sm font-medium text-rose-600">{event.cost}</div>
          )}

          {/* Description */}
          {event.description && (
            <CollapsibleText
              text={stripHtml(event.description)}
              className="text-sm text-gray-600 leading-relaxed whitespace-pre-line border-t border-gray-100 pt-3 mt-1"
            />
          )}

          {/* Action buttons */}
          <div className="flex flex-wrap gap-2 mt-2">
            {event.url && (
              <a href={event.url} target="_blank" rel="noopener" className="pretty-pill pretty-pill-rose">
                Event Page
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
          </div>
        </div>
      </div>
    </div>
  );
}
