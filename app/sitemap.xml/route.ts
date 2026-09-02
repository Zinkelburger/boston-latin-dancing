import allEvents from '@/data/events-published.json';
import type { DanceEvent } from '@/types/event';
import { SITE_URL } from '@/lib/constants';

export const dynamic = 'force-static';

const events = allEvents as DanceEvent[];

function escapeXml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function GET() {
  // Keep recurring events in the sitemap even once archived: the series will
  // recur, and the page accumulates ranking authority over time. Only one-off
  // events that are done drop out.
  const slugs = new Set<string>();
  for (const e of events) {
    if (e.slug && (!e.archived || e.recurring)) slugs.add(e.slug);
  }
  const eventEntries = [...slugs].map(
    slug => `  <url>
    <loc>${escapeXml(`${SITE_URL}/event/${slug}`)}</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>`,
  );

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
