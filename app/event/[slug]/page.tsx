import type { Metadata } from 'next';
import allEvents from '@/data/events-published.json';
import type { DanceEvent } from '@/types/event';
import { SITE_URL, STYLE_LABELS } from '@/lib/constants';
import { formatEventTimeRange, getRecurrenceLabel } from '@/lib/recurrences';
import { stripHtml } from '@/lib/strip-html';
import EventJsonLd from './EventJsonLd';
import MapView from '@/app/components/MapView';

const events = allEvents as DanceEvent[];

type Params = { slug: string };

function findBySlug(slug: string): DanceEvent | undefined {
  return events.find(e => e.slug === slug);
}

function findActiveInstance(event: DanceEvent): DanceEvent | undefined {
  if (!event.archived) return undefined;
  const norm = event.name.toLowerCase().replace(/[^\w\s]/g, '').replace(/\s+/g, ' ').trim();
  const venueA = (event.location || '').split(',')[0].toLowerCase().trim();
  return events.find(e => {
    if (e.archived || !e.slug) return false;
    const cn = e.name.toLowerCase().replace(/[^\w\s]/g, '').replace(/\s+/g, ' ').trim();
    if (cn === norm) return true;
    if (cn.includes(norm) || norm.includes(cn)) {
      const venueB = (e.location || '').split(',')[0].toLowerCase().trim();
      return venueA && venueB && venueA === venueB;
    }
    return false;
  });
}

export function generateStaticParams(): Params[] {
  return events.filter(e => e.slug).map(e => ({ slug: e.slug! }));
}

/**
 * First ~150 chars of the scraped description, cleaned up for use as a search
 * snippet / link preview. Cuts at a sentence or word boundary rather than
 * mid-word, and returns '' for text that would read as spam (emoji walls,
 * ALL-CAPS promo copy) — in that case the "date — venue" line stands alone.
 */
function descriptionSnippet(raw: string | undefined, max = 150): string {
  const text = stripHtml(raw || '')
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/www\.\S+/gi, '')
    .replace(/Source:\s*/gi, '')
    .replace(/Website:\s*/gi, '')
    .replace(/Organized by\s+\S+\s*/gi, '')
    .replace(/\s+/g, ' ')
    .trim();

  if (text.length < 25) return '';
  const emoji = (text.match(/\p{Extended_Pictographic}/gu) || []).length;
  if (emoji > 2) return '';
  const letters = (text.match(/[a-zA-Z]/g) || []).length;
  const upper = (text.match(/[A-Z]/g) || []).length;
  if (letters >= 20 && upper / letters > 0.5) return '';

  if (text.length <= max) return text;
  const cut = text.slice(0, max + 1);
  const lastSentence = Math.max(
    cut.lastIndexOf('. '), cut.lastIndexOf('! '), cut.lastIndexOf('? '));
  if (lastSentence > max * 0.5) return cut.slice(0, lastSentence + 1);
  const lastSpace = cut.lastIndexOf(' ');
  return (lastSpace > 0 ? cut.slice(0, lastSpace) : cut.slice(0, max)).trimEnd() + '…';
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { slug } = await params;
  const event = findBySlug(slug);
  if (!event) {
    return { title: 'Event Not Found' };
  }

  const venue = event.location?.split(',')[0] || '';
  // Search-only venue records are dateless; the recurrence label stands in.
  const date = event.startDate
    ? new Date(event.startDate).toLocaleDateString('en-US', {
        weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/New_York',
      })
    : '';

  const snippet = descriptionSnippet(event.description);
  const when = [date, venue].filter(Boolean).join(' — ');
  const description = snippet
    ? `${when}. ${snippet}`
    : when;

  const url = `${SITE_URL}/event/${slug}`;
  const activeInstance = findActiveInstance(event);
  const canonicalUrl = activeInstance
    ? `${SITE_URL}/event/${activeInstance.slug}`
    : url;

  // Title carries the "when" so our listing reads differently from the
  // organizer's / ticketing platform's result and gives a reason to click.
  const whenLabel = event.recurring
    ? (getRecurrenceLabel(event) || `Every ${event.dayOfWeek}`)
    : date;
  const pageTitle = whenLabel
    ? `${event.name} — ${whenLabel} | Boston Salsa`
    : `${event.name} | Boston Salsa`;

  // Recurring series keep accumulating SEO authority even after a given run of
  // dates passes — the event comes back. Only one-off events that are truly
  // done get noindexed.
  const noindex = event.archived && !event.recurring;

  return {
    title: { absolute: pageTitle },
    description,
    ...(noindex ? { robots: { index: false, follow: true } } : {}),
    openGraph: {
      title: event.name,
      description,
      url: canonicalUrl,
      siteName: 'Boston Salsa Events',
      type: 'website',
    },
    twitter: {
      card: 'summary',
      title: event.name,
      description,
    },
    alternates: {
      canonical: canonicalUrl,
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
  const isMappable = event.lat != null && event.lng != null;
  const cleanDesc = stripHtml(event.description)
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/www\.\S+/gi, '')
    .replace(/\s+/g, ' ')
    .slice(0, 300).trim();

  const breadcrumb = {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: [
      { '@type': 'ListItem', position: 1, name: 'Boston Salsa Events', item: SITE_URL },
      { '@type': 'ListItem', position: 2, name: event.name, item: shareUrl },
    ],
  };

  return (
    <>
      <EventJsonLd event={event} url={shareUrl} />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumb) }}
      />
      {isMappable ? (
        <div className="h-full w-full overflow-hidden">
          <div className="sr-only">
            <h1>{event.name}</h1>
            {event.startDate && <p>{formatEventTimeRange(event.startDate, event.endDate)}</p>}
            <p>{event.location}</p>
            {event.cost && <p>{event.cost}</p>}
            {cleanDesc && <p>{cleanDesc}</p>}
          </div>
          <MapView initialEventSlug={slug} />
        </div>
      ) : (
        <main className="min-h-screen bg-gray-50">
          <div className="mx-auto max-w-2xl px-4 py-10">
            <h1 className="text-2xl font-bold text-gray-900">{event.name}</h1>
            {event.startDate && (
              <p className="mt-2 text-sm text-gray-700">
                {formatEventTimeRange(event.startDate, event.endDate)}
              </p>
            )}
            {event.location && (
              <p className="mt-1 text-sm text-gray-600">{event.location}</p>
            )}
            {event.cost && (
              <p className="mt-2 text-sm font-medium text-rose-700">{event.cost}</p>
            )}
            {cleanDesc && (
              <p className="mt-4 text-sm leading-6 text-gray-700">{cleanDesc}</p>
            )}
            <div className="mt-6">
              <a href="/" className="pretty-pill pretty-pill-rose">
                Back to map
              </a>
            </div>
          </div>
        </main>
      )}
    </>
  );
}
