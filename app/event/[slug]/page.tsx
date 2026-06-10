import type { Metadata } from 'next';
import allEvents from '@/data/events-published.json';
import type { DanceEvent } from '@/types/event';
import { SITE_URL, STYLE_LABELS } from '@/lib/constants';
import { formatEventTimeRange } from '@/lib/recurrences';
import { stripHtml } from '@/lib/strip-html';
import EventJsonLd from './EventJsonLd';
import EventRedirect from './EventRedirect';

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

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { slug } = await params;
  const event = findBySlug(slug);
  if (!event) {
    return { title: 'Event Not Found' };
  }

  const venue = event.location?.split(',')[0] || '';
  const date = new Date(event.startDate).toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });

  const snippet = stripHtml(event.description)
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/www\.\S+/gi, '')
    .replace(/Source:\s*/gi, '')
    .replace(/Website:\s*/gi, '')
    .replace(/Organized by\s+\S+\s*/gi, '')
    .replace(/\s+/g, ' ')
    .slice(0, 120).trim();
  const when = [date, venue].filter(Boolean).join(' — ');
  const description = snippet
    ? `${when}. ${snippet}`
    : when;

  const url = `${SITE_URL}/event/${slug}`;
  const activeInstance = findActiveInstance(event);
  const canonicalUrl = activeInstance
    ? `${SITE_URL}/event/${activeInstance.slug}`
    : url;

  return {
    title: event.name,
    description,
    ...(event.archived ? { robots: { index: false, follow: true } } : {}),
    openGraph: {
      title: event.name,
      description: event.organizer ? `${description} — ${event.organizer}` : description,
      url: canonicalUrl,
      siteName: 'Boston Salsa Events',
      type: 'website',
    },
    twitter: {
      card: 'summary',
      title: event.name,
      description: event.organizer ? `${description} — ${event.organizer}` : description,
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
  const mapUrl = `/#event=${slug}`;
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
      <EventRedirect to={mapUrl} />
      {/* Server-rendered content for SEO — visible briefly before redirect */}
      <div className="max-w-lg mx-auto px-4 py-8">
        <h1 className="text-xl font-semibold mb-2">{event.name}</h1>
        <p className="text-sm text-gray-600 mb-1">
          {formatEventTimeRange(event.startDate, event.endDate)}
        </p>
        <p className="text-sm text-gray-500 mb-2">{event.location}</p>
        {event.cost && (
          <p className="text-sm font-medium text-rose-600 mb-2">{event.cost}</p>
        )}
        {cleanDesc && (
          <p className="text-sm text-gray-600">{cleanDesc}</p>
        )}
      </div>
    </>
  );
}
