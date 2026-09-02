import type { Metadata } from 'next';
import allEvents from '@/data/events-published.json';
import type { DanceEvent } from '@/types/event';
import Link from 'next/link';
import { SITE_URL } from '@/lib/constants';
import { formatEventTimeRange, getRecurrenceLabel, occurrenceEndDate } from '@/lib/recurrences';
import { displayStartIso, hasStartDate } from '@/lib/dates';
import { findActiveInstance } from '@/lib/search';
import { stripHtml } from '@/lib/strip-html';
import { cleanDisplayText } from '@/lib/display-text';
import EventJsonLd from './EventJsonLd';
import MapView from '@/app/components/MapView';
import { redirectTarget, retiredName, retiredSlugs } from '@/lib/slug-registry';

const events = allEvents as DanceEvent[];

type Params = { slug: string };

function findBySlug(slug: string): DanceEvent | undefined {
  return events.find(e => e.slug === slug);
}

export function generateStaticParams(): Params[] {
  // Retired slugs get a page too. A URL we have published is a URL someone may
  // have indexed, bookmarked or shared, and the static export is the only
  // chance to answer it — there is no server to redirect at request time.
  const live = events.flatMap(e => (e.slug ? [e.slug] : []));
  return [...new Set([...live, ...retiredSlugs()])].map(slug => ({ slug }));
}

/** "Sat, Sep 6" for the next occurrence; '' for dateless venue records. */
function displayDateLabel(event: DanceEvent): string {
  if (!hasStartDate(event)) return '';
  return new Date(displayStartIso(event)).toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/New_York',
  });
}

/** The occurrence to describe on the page: the next one for a series. */
function displayTimeRange(event: DanceEvent): string | null {
  if (!hasStartDate(event)) return null;
  const start = displayStartIso(event);
  return formatEventTimeRange(start, occurrenceEndDate(event, start));
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
    const target = redirectTarget(slug);
    if (target) {
      // Point every signal at the replacement so the ranking this URL earned
      // moves there rather than evaporating.
      const moved = findBySlug(target);
      return {
        title: { absolute: `${moved?.name ?? 'Event'} | Boston Salsa` },
        alternates: { canonical: `${SITE_URL}/event/${target}` },
        robots: { index: false, follow: true },
      };
    }
    const name = retiredName(slug);
    return {
      title: { absolute: name ? `${name} — ended | Boston Salsa` : 'Event ended | Boston Salsa' },
      description: 'This event has ended. See what else is on in Boston.',
      robots: { index: false, follow: true },
    };
  }

  const venue = event.location?.split(',')[0] || '';
  // Search-only venue records are dateless; the recurrence label stands in.
  const date = displayDateLabel(event);

  const snippet = descriptionSnippet(event.description);
  const when = [date, venue].filter(Boolean).join(' — ');
  const description = snippet
    ? `${when}. ${snippet}`
    : when;

  const url = `${SITE_URL}/event/${slug}`;
  const activeInstance = findActiveInstance(event, events);
  const canonicalUrl = activeInstance
    ? `${SITE_URL}/event/${activeInstance.slug}`
    : url;

  // Title carries the "when" so our listing reads differently from the
  // organizer's / ticketing platform's result and gives a reason to click.
  // Never invent "Every Sunday" from dayOfWeek alone — a monthly first-Sunday
  // series with a rain date in recurrences[] used to get that fallback.
  const whenLabel = event.recurring
    ? (getRecurrenceLabel(event) || date)
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
    const target = redirectTarget(slug);
    if (target) {
      const moved = findBySlug(target);
      const href = `/event/${target}`;
      // A static export has no server to issue a 301, so the redirect has to
      // travel in the page: an instant meta refresh (which Google follows and
      // treats as a permanent move), plus a real link so anyone whose browser
      // ignores it — or who arrives with JS off — still gets there.
      return (
        <>
          <meta httpEquiv="refresh" content={`0; url=${href}`} />
          <div className="min-h-screen flex items-center justify-center bg-gray-50">
            <div className="text-center px-6">
              <h1 className="text-2xl font-bold text-gray-800 mb-2">This event moved</h1>
              <p className="text-gray-500 mb-4">
                {moved ? `It is now listed as “${moved.name}”.` : 'Taking you to its new page.'}
              </p>
              <a href={href} className="pretty-pill pretty-pill-rose">Go to the event</a>
            </div>
          </div>
        </>
      );
    }

    const name = retiredName(slug);
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center px-6">
          <h1 className="text-2xl font-bold text-gray-800 mb-2">
            {name ? `${name} has ended` : 'This event has ended'}
          </h1>
          <p className="text-gray-500 mb-4">
            It is no longer on the calendar — but plenty else is.
          </p>
          <Link href="/" className="pretty-pill pretty-pill-rose">See what’s on</Link>
        </div>
      </div>
    );
  }

  const shareUrl = `${SITE_URL}/event/${slug}`;
  const isMappable = event.lat != null && event.lng != null;
  const timeRange = displayTimeRange(event);
  const cleanDesc = cleanDisplayText(event.description)
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
            {timeRange && <p>{timeRange}</p>}
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
            {timeRange && (
              <p className="mt-2 text-sm text-gray-700">
                {timeRange}
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
              <Link href="/" className="pretty-pill pretty-pill-rose">
                Back to map
              </Link>
            </div>
          </div>
        </main>
      )}
    </>
  );
}
