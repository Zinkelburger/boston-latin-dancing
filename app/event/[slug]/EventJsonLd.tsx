import type { DanceEvent } from '@/types/event';
import { stripHtml } from '@/lib/strip-html';

interface Props {
  event: DanceEvent;
  url: string;
}

export default function EventJsonLd({ event, url }: Props) {
  // No structured data for past events, or for dateless search-only venue
  // records (schema.org Event requires a startDate).
  if (event.archived || !event.startDate) return null;

  const venueName = event.location?.split('\n')[0]?.split(',')[0] || '';

  const jsonLd: Record<string, unknown> = {
    '@context': 'https://schema.org',
    '@type': 'DanceEvent',
    name: event.name,
    startDate: event.startDate,
    endDate: event.endDate,
    url,
    eventAttendanceMode: 'https://schema.org/OfflineEventAttendanceMode',
    eventStatus: 'https://schema.org/EventScheduled',
    location: {
      '@type': 'Place',
      name: venueName,
      address: event.location || undefined,
      ...(event.lat && event.lng
        ? { geo: { '@type': 'GeoCoordinates', latitude: event.lat, longitude: event.lng } }
        : {}),
    },
  };

  if (event.description) {
    jsonLd.description = stripHtml(event.description).slice(0, 300);
  }

  if (event.organizer) {
    jsonLd.organizer = {
      '@type': 'Organization',
      name: event.organizer,
    };
  }

  if (event.cost) {
    const isFree = /free/i.test(event.cost);
    jsonLd.offers = {
      '@type': 'Offer',
      price: isFree ? '0' : undefined,
      priceCurrency: 'USD',
      availability: 'https://schema.org/InStock',
      url: event.url || url,
      description: event.cost,
    };
  }

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
    />
  );
}
