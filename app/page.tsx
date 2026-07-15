import type { Metadata } from 'next';
import allEvents from '@/data/events-published.json';
import type { DanceEvent } from '@/types/event';
import { SITE_URL } from '@/lib/constants';
import { stripHtml } from '@/lib/strip-html';
import { formatEventTimeRange, firstOccurrenceInRange } from '@/lib/recurrences';
import MapView from './components/MapView';

const events = (allEvents as DanceEvent[]).filter(e => !e.archived && !e.searchOnly && e.slug);

/**
 * Count of events with an occurrence in the next `withinDays`. Computed at
 * build so the homepage description stays fresh without embedding raw scraped
 * event names, which read as machine-generated in search snippets and
 * WhatsApp previews.
 */
function upcomingEventCount(withinDays = 7): number {
  const now = Date.now();
  const end = now + withinDays * 86400000;
  return events.filter(e => firstOccurrenceInRange(e, now, end)).length;
}

const upcomingCount = upcomingEventCount();
const homeDescription = upcomingCount >= 5
  ? `Every salsa and bachata night in Boston on one live map — `
    + `${upcomingCount} socials, classes, and parties this week.`
  : 'Where to dance salsa and bachata in Boston. A live map of Latin dance '
    + 'socials, parties, and classes happening this week near you.';

export const metadata: Metadata = {
  title: {
    absolute: 'Salsa & Bachata Events in Boston This Week | bostonsalsa.org',
  },
  description: homeDescription,
  alternates: { canonical: SITE_URL },
  openGraph: {
    title: 'Salsa & Bachata Events in Boston This Week',
    description: homeDescription,
    type: 'website',
    url: SITE_URL,
  },
};

function HomeJsonLd() {
  const itemList = {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    name: 'Boston Salsa Events',
    description: 'Salsa and Latin dance events in the Boston area — classes, socials, and parties.',
    numberOfItems: events.length,
    itemListElement: events.slice(0, 50).map((e, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      item: {
        '@type': 'DanceEvent',
        name: e.name,
        startDate: e.startDate,
        endDate: e.endDate,
        url: `${SITE_URL}/event/${e.slug}`,
        ...(e.location ? {
          location: {
            '@type': 'Place',
            name: e.location.split(',')[0],
            address: e.location,
            ...(e.lat != null && e.lng != null ? {
              geo: { '@type': 'GeoCoordinates', latitude: e.lat, longitude: e.lng },
            } : {}),
          },
        } : {}),
        ...(e.cost ? { offers: { '@type': 'Offer', price: e.cost } } : {}),
      },
    })),
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(itemList) }}
    />
  );
}

export default function Home() {
  return (
    <div className="h-full w-full overflow-hidden">
      <h1 className="sr-only">Salsa &amp; Latin Dance Events in Boston This Week</h1>
      <p className="sr-only">
        Where to dance salsa and bachata in Boston. Find Latin dance socials,
        parties, and classes happening tonight, this weekend, and all week.
        Updated daily.
      </p>
      <HomeJsonLd />
      <MapView />
      <section className="sr-only" aria-label="Event listings">
        <h2>Upcoming Events</h2>
        {events.map(e => {
          const desc = stripHtml(e.description)
            .replace(/https?:\/\/\S+/gi, '')
            .replace(/www\.\S+/gi, '')
            .replace(/\s+/g, ' ')
            .slice(0, 200).trim();
          return (
            <article key={e.id}>
              <h3>
                <a href={`/event/${e.slug}`}>{e.name}</a>
              </h3>
              <p>{formatEventTimeRange(e.startDate, e.endDate)}</p>
              {e.location && <p>{e.location}</p>}
              {e.styles.length > 0 && (
                <p>Styles: {e.styles.join(', ')}</p>
              )}
              {e.cost && <p>{e.cost}</p>}
              {desc && <p>{desc}</p>}
            </article>
          );
        })}
      </section>
    </div>
  );
}
