import type { Metadata } from 'next';
import allEvents from '@/data/events-published.json';
import type { DanceEvent } from '@/types/event';
import { SITE_URL, STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';
import { formatEventTimeRange, getRecurrenceLabel } from '@/lib/recurrences';
import { stripHtml } from '@/lib/strip-html';
import EventDetailClient from './EventDetailClient';
import CollapsibleText from '@/app/components/CollapsibleText';
import { UpcomingDatesTable, WeeklyScheduleTable } from '@/app/components/EventTable';
import EventJsonLd from './EventJsonLd';

const events = allEvents as DanceEvent[];

type Params = { slug: string };

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
      description: event.organizer ? `${description} — ${event.organizer}` : description,
      url,
      siteName: 'Boston Latin Dance Map',
      type: 'website',
    },
    twitter: {
      card: 'summary',
      title: event.name,
      description: event.organizer ? `${description} — ${event.organizer}` : description,
    },
    alternates: {
      canonical: url,
    },
  };
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
  const shareText = `${event.name} — ${formatEventTimeRange(event.startDate, event.endDate)}`;
  const recurrenceLabel = getRecurrenceLabel(event);

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

        <EventJsonLd event={event} url={shareUrl} />

        {event.archived && (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <strong>This event has passed.</strong>{' '}
            <a href="/" className="underline hover:text-amber-900">Browse upcoming events</a>
          </div>
        )}

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
            {event.recurring && !recurrenceLabel && (
              <span className="pretty-pill pretty-pill-neutral text-xs">Recurring</span>
            )}
          </div>

          {recurrenceLabel && !(event.schedule && event.schedule.length > 0) && (
            <span className="pretty-pill pretty-pill-sky text-xs" style={{ alignSelf: 'flex-start' }}>
              {recurrenceLabel}
            </span>
          )}

          {!(event.schedule && event.schedule.length > 0) && (
            <div className="text-sm text-gray-600">
              {formatEventTimeRange(event.startDate, event.endDate)}
            </div>
          )}

          <UpcomingDatesTable event={event} className="mt-1" />

          {event.schedule && event.schedule.length > 0 && (
            <WeeklyScheduleTable schedule={event.schedule} className="mt-1" />
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
