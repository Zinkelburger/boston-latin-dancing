import allEvents from '@/data/events-published.json';
import type { DanceEvent } from '@/types/event';
import { SITE_URL } from '@/lib/constants';

export const dynamic = 'force-static';

const events = allEvents as DanceEvent[];

function escapeXml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function GET() {
  const eventEntries = events
    .filter(e => e.slug && !e.archived)
    .map(e => `  <url>
    <loc>${escapeXml(`${SITE_URL}/event/${e.slug}`)}</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>`);

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>${escapeXml(SITE_URL)}</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>${escapeXml(`${SITE_URL}/submit`)}</loc>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
${eventEntries.join('\n')}
</urlset>`;

  return new Response(xml, {
    headers: { 'Content-Type': 'application/xml' },
  });
}
