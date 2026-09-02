import type { Metadata } from 'next';
import allEvents from '@/data/events-published.json';
import type { DanceEvent } from '@/types/event';
import { SITE_URL } from '@/lib/constants';
import { formatEventTimeRange, occurrenceEndDate } from '@/lib/recurrences';
import { displayStartIso } from '@/lib/dates';
import { offerFromCost } from '@/lib/offer';
import MapView from './components/MapView';
import { cleanDisplayText } from '@/lib/display-text';

const events = (allEvents as DanceEvent[]).filter(e => !e.archived && !e.searchOnly && e.slug);

/**
 * The count of listed events is a build-time fact about the data, so it is
 * safe to bake into the description. A "this week" count was not: it depended
 * on the clock at build time and went stale between builds.
 */
const homeDescription = events.length >= 5
  ? `Every salsa and bachata night in Boston on one live map — `
    + `${events.length} socials, classes, and parties, updated daily.`
  : 'Where to dance salsa and bachata in Boston. A live map of Latin dance '
    + 'socials, parties, and classes near you, updated daily.';

export const metadata: Metadata = {
  title: {
    absolute: 'Boston Salsa Events | bostonsalsa.org',
  },
  description: homeDescription,
  alternates: { canonical: SITE_URL },
  openGraph: {
    title: 'Boston Salsa Events',
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
    itemListElement: events.slice(0, 50).map((e, i) => {
      const startDate = displayStartIso(e);
      const offer = offerFromCost(e.cost, e.url);
      return {
        '@type': 'ListItem',
        position: i + 1,
        item: {
          '@type': 'DanceEvent',
          name: e.name,
          startDate,
          endDate: occurrenceEndDate(e, startDate),
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
          ...(offer ? { offers: offer } : {}),
        },
      };
    }),
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
          const desc = cleanDisplayText(e.description)
            .replace(/\s+/g, ' ')
            .slice(0, 200).trim();
          const start = displayStartIso(e);
          return (
            <article key={e.id}>
              <h3>
                <a href={`/event/${e.slug}`}>{e.name}</a>
              </h3>
              <p>{formatEventTimeRange(start, occurrenceEndDate(e, start))}</p>
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
