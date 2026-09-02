import type { DanceEvent } from '@/types/event';
import { cleanDisplayText } from '@/lib/display-text';
import { displayStartIso, hasStartDate } from '@/lib/dates';
import { occurrenceEndDate } from '@/lib/recurrences';
import { offerFromCost } from '@/lib/offer';

interface Props {
  event: DanceEvent;
  url: string;
}

export default function EventJsonLd({ event, url }: Props) {
  // No structured data for past events, or for dateless search-only venue
  // records (schema.org Event requires a startDate).
  if (event.archived || !hasStartDate(event)) return null;

  const venueName = event.location?.split('\n')[0]?.split(',')[0] || '';
  // Recurring series: advertise the next occurrence, not the one publish()
  // happened to see first.
  const startDate = displayStartIso(event);
  const endDate = occurrenceEndDate(event, startDate);

  const jsonLd: Record<string, unknown> = {
    '@context': 'https://schema.org',
    '@type': 'DanceEvent',
    name: event.name,
    startDate,
    endDate,
    url,
    eventAttendanceMode: 'https://schema.org/OfflineEventAttendanceMode',
    eventStatus: 'https://schema.org/EventScheduled',
    location: {
      '@type': 'Place',
      name: venueName,
      address: event.location || undefined,
      ...(event.lat != null && event.lng != null
        ? { geo: { '@type': 'GeoCoordinates', latitude: event.lat, longitude: event.lng } }
        : {}),
    },
  };

  if (event.description) {
    jsonLd.description = cleanDisplayText(event.description).slice(0, 300);
  }

  if (event.organizer) {
    jsonLd.organizer = {
      '@type': 'Organization',
      name: event.organizer,
    };
  }

  const offer = offerFromCost(event.cost, event.url || url);
  if (offer) jsonLd.offers = offer;

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
    />
  );
}
