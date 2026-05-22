import type { MetadataRoute } from 'next';
import allEvents from '@/public/events.json';
import type { DanceEvent } from '@/types/event';
import { SITE_URL, STYLE_SLUGS } from '@/lib/constants';

export const dynamic = 'force-static';

const events = allEvents as DanceEvent[];

export default function sitemap(): MetadataRoute.Sitemap {
  const styleEntries = STYLE_SLUGS
    .filter(s => s !== 'other')
    .map(s => ({
      url: `${SITE_URL}/style/${s}`,
      lastModified: new Date(),
      changeFrequency: 'weekly' as const,
      priority: 0.9,
    }));

  const eventEntries = events
    .filter(e => e.slug)
    .map(e => ({
      url: `${SITE_URL}/event/${e.slug}`,
      lastModified: new Date(),
      changeFrequency: 'weekly' as const,
      priority: 0.8,
    }));

  return [
    {
      url: SITE_URL,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 1,
    },
    {
      url: `${SITE_URL}/submit`,
      lastModified: new Date(),
      changeFrequency: 'monthly',
      priority: 0.5,
    },
    ...styleEntries,
    ...eventEntries,
  ];
}
