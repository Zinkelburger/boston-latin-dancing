import type { Metadata } from 'next';
import Link from 'next/link';
import allEvents from '@/data/events-published.json';
import type { DanceEvent, DanceStyle } from '@/types/event';
import {
  SITE_URL,
  STYLE_LABELS,
  STYLE_DESCRIPTIONS,
  STYLE_PILL_CLASS,
  STYLE_SLUGS,
  STYLE_COLORS,
} from '@/lib/constants';
import { CompactScheduleTable } from '@/app/components/EventTable';
import { recurringWhenLabel } from '@/lib/recurrences';

const events = allEvents as DanceEvent[];

type Params = { style: string };

function isValidStyle(s: string): s is DanceStyle {
  return STYLE_SLUGS.includes(s as DanceStyle);
}

function eventsForStyle(style: DanceStyle): DanceEvent[] {
  return events
    .filter(e => e.styles.includes(style) && e.slug)
    .sort((a, b) => new Date(a.startDate).getTime() - new Date(b.startDate).getTime());
}

export function generateStaticParams(): Params[] {
  return STYLE_SLUGS.filter(s => s !== 'other').map(s => ({ style: s }));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { style } = await params;
  if (!isValidStyle(style)) {
    return { title: 'Style Not Found | Boston Latin Dance Map' };
  }

  const label = STYLE_LABELS[style];
  const title = `${label} Events in Boston | Boston Latin Dance Map`;
  const description = STYLE_DESCRIPTIONS[style];
  const url = `${SITE_URL}/style/${style}`;

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      url,
      siteName: 'Boston Latin Dance Map',
      type: 'website',
    },
    twitter: {
      card: 'summary',
      title,
      description,
    },
    alternates: { canonical: url },
  };
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });
}

export default async function StylePage({ params }: { params: Promise<Params> }) {
  const { style } = await params;

  if (!isValidStyle(style)) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-gray-800 mb-2">Style Not Found</h1>
          <p className="text-gray-500 mb-4">We don&apos;t have a page for that dance style.</p>
          <Link href="/" className="pretty-pill pretty-pill-rose">Back to Map</Link>
        </div>
      </div>
    );
  }

  const label = STYLE_LABELS[style];
  const color = STYLE_COLORS[style];
  const matching = eventsForStyle(style);
  const now = Date.now();

  const upcoming = matching.filter(e => new Date(e.startDate).getTime() >= now - 86400000 || e.recurring);
  const past = matching.filter(e => new Date(e.startDate).getTime() < now - 86400000 && !e.recurring);

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-2xl mx-auto px-4 py-8">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-600 mb-6"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="19" y1="12" x2="5" y2="12" />
            <polyline points="12 19 5 12 12 5" />
          </svg>
          Back to Map
        </Link>

        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">
            <span style={{ color }}>{label}</span> Events in Boston
          </h1>
          <p className="text-gray-500">{STYLE_DESCRIPTIONS[style]}</p>
        </div>

        {/* Other style links */}
        <div className="flex flex-wrap gap-2 mb-8">
          {STYLE_SLUGS.filter(s => s !== 'other' && s !== style).map(s => (
            <Link
              key={s}
              href={`/style/${s}`}
              className={`pretty-pill ${STYLE_PILL_CLASS[s]} text-xs`}
            >
              {STYLE_LABELS[s]}
            </Link>
          ))}
        </div>

        {upcoming.length === 0 && past.length === 0 && (
          <p className="text-gray-400 text-center py-12">
            No {label.toLowerCase()} events found right now. Check back soon!
          </p>
        )}

        {upcoming.length > 0 && (
          <section className="mb-10">
            <h2 className="text-lg font-semibold text-gray-700 mb-4">
              Upcoming &amp; Recurring ({upcoming.length})
            </h2>
            <div className="flex flex-col gap-3">
              {upcoming.map(event => (
                <EventCard key={event.id} event={event} style={style} />
              ))}
            </div>
          </section>
        )}

        {past.length > 0 && (
          <section>
            <h2 className="text-lg font-semibold text-gray-400 mb-4">
              Past ({past.length})
            </h2>
            <div className="flex flex-col gap-3 opacity-60">
              {past.map(event => (
                <EventCard key={event.id} event={event} style={style} />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function EventCard({ event, style }: { event: DanceEvent; style: DanceStyle }) {
  const whenLabel = recurringWhenLabel(event) ?? formatDate(event.startDate);

  return (
    <Link
      href={`/event/${event.slug}`}
      className="block bg-white rounded-xl p-4 shadow-sm hover:shadow-md transition-shadow"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-gray-900 truncate">{event.name}</h3>
          <div className="text-sm text-gray-500 mt-0.5">{whenLabel}</div>
          {event.location && (
            <div className="text-sm text-gray-400 mt-0.5 truncate">
              {event.location.split('\n')[0]}
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-1 shrink-0">
          {event.styles.map(s => (
            <span
              key={s}
              className={`pretty-pill ${STYLE_PILL_CLASS[s]} text-xs`}
              style={s === style ? { fontWeight: 600 } : undefined}
            >
              {STYLE_LABELS[s]}
            </span>
          ))}
        </div>
      </div>
      {event.schedule && event.schedule.length > 0 && (
        <CompactScheduleTable schedule={event.schedule} className="event-table-compact mt-2" />
      )}
      {event.cost && (
        <div className="text-xs font-medium text-rose-600 mt-2">{event.cost}</div>
      )}
    </Link>
  );
}
