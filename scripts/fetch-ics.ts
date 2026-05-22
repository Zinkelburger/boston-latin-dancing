/**
 * Fetches the public Google Calendar ICS feed for Greater Boston Dance Socials,
 * parses it, classifies events by dance style, and extracts geo coordinates.
 * Outputs public/events.json for the frontend.
 */

import { writeFileSync, readFileSync, existsSync } from 'fs';
import { resolve } from 'path';
import { slugify } from '../lib/slugify';

const ICS_URL =
  'https://calendar.google.com/calendar/ical/31d111cd5f84b2c5cde57a9a175e4769da698d758828fb8de8b47158eefb819c%40group.calendar.google.com/public/basic.ics';

type DanceStyle = 'bachata' | 'salsa' | 'kizomba' | 'zouk' | 'merengue' | 'other';

type DayOfWeek =
  | 'Monday' | 'Tuesday' | 'Wednesday'
  | 'Thursday' | 'Friday' | 'Saturday' | 'Sunday';

interface DanceEvent {
  id: string;
  slug: string;
  name: string;
  startDate: string;
  endDate: string;
  dayOfWeek: DayOfWeek;
  location: string;
  lat: number | null;
  lng: number | null;
  description: string;
  url: string | null;
  styles: DanceStyle[];
  cost: string | null;
  recurring: boolean;
}

const DAYS: DayOfWeek[] = [
  'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
];

const STYLE_PATTERNS: [DanceStyle, RegExp][] = [
  ['bachata', /bachata/i],
  ['salsa', /salsa/i],
  ['kizomba', /kizomba/i],
  ['zouk', /zouk/i],
  ['merengue', /merengue/i],
];

function detectStyles(text: string): DanceStyle[] {
  const found: DanceStyle[] = [];
  for (const [style, re] of STYLE_PATTERNS) {
    if (re.test(text)) found.push(style);
  }
  if (found.length === 0) found.push('other');
  return found;
}

function extractCost(text: string): string | null {
  const patterns = [
    /(?:cover|cost|admission|entry|ticket)[:\s]*\$?\s*(\$?\d+(?:\s*[-–]\s*\$?\d+)?)/i,
    /\$(\d+)\s*(?:at\s+(?:the\s+)?door|online|advance)/i,
    /(\$\d+(?:\s*[-–\/]\s*\$?\d+)?)\s*(?:at\s+(?:the\s+)?door|online|advance|cover|entry)/i,
    /(?:FREE|free)\s+(?:EVENT|event)/i,
  ];

  for (const pat of patterns) {
    const m = text.match(pat);
    if (m) {
      if (/free/i.test(m[0])) return 'Free';
      return m[0].trim();
    }
  }

  const dollarMatch = text.match(/\$\d+/);
  if (dollarMatch) return dollarMatch[0];

  if (/\bfree\b/i.test(text)) return 'Free';
  return null;
}

function unfoldLines(ics: string): string {
  return ics.replace(/\r\n[ \t]/g, '').replace(/\n[ \t]/g, '');
}

function parseIcs(raw: string): DanceEvent[] {
  const unfolded = unfoldLines(raw);
  const events: DanceEvent[] = [];

  const eventBlocks = unfolded.split('BEGIN:VEVENT');
  for (let i = 1; i < eventBlocks.length; i++) {
    const block = eventBlocks[i].split('END:VEVENT')[0];
    const lines = block.split(/\r?\n/);

    const props: Record<string, string> = {};
    for (const line of lines) {
      const colonIdx = line.indexOf(':');
      if (colonIdx < 0) continue;
      const key = line.slice(0, colonIdx).split(';')[0].trim();
      const value = line.slice(colonIdx + 1).trim();
      if (key && !props[key]) {
        props[key] = value;
      }
    }

    const uid = props['UID'] ?? `event-${i}`;
    const summary = props['SUMMARY'] ?? 'Untitled Event';
    const description = (props['DESCRIPTION'] ?? '')
      .replace(/\\n/g, '\n')
      .replace(/\\,/g, ',')
      .replace(/\\\\/g, '\\');
    const location = (props['LOCATION'] ?? '')
      .replace(/\\n/g, '\n')
      .replace(/\\,/g, ',');
    const url = props['URL'] ?? null;
    const recurring = !!props['RRULE'];

    let startDate: Date;
    const dtstart = props['DTSTART'] ?? '';
    if (dtstart.endsWith('Z')) {
      startDate = new Date(
        dtstart.replace(/(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/, '$1-$2-$3T$4:$5:$6Z')
      );
    } else if (dtstart.includes('T')) {
      startDate = new Date(
        dtstart.replace(/(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/, '$1-$2-$3T$4:$5:$6')
      );
    } else if (dtstart.length >= 8) {
      startDate = new Date(
        dtstart.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3')
      );
    } else {
      continue;
    }

    if (isNaN(startDate.getTime())) continue;

    let endDate: Date;
    const dtend = props['DTEND'] ?? '';
    if (dtend.endsWith('Z')) {
      endDate = new Date(
        dtend.replace(/(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/, '$1-$2-$3T$4:$5:$6Z')
      );
    } else if (dtend.includes('T')) {
      endDate = new Date(
        dtend.replace(/(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/, '$1-$2-$3T$4:$5:$6')
      );
    } else {
      endDate = startDate;
    }

    const combinedText = `${summary} ${description}`;
    const styles = detectStyles(combinedText);
    const cost = extractCost(combinedText + ' ' + description);

    const dayOfWeek = DAYS[startDate.getDay()];

    events.push({
      id: uid,
      slug: slugify(summary, uid),
      name: summary,
      startDate: startDate.toISOString(),
      endDate: endDate.toISOString(),
      dayOfWeek,
      location,
      lat: null,
      lng: null,
      description,
      url: url && !url.startsWith('fb://') ? url : null,
      styles,
      cost,
      recurring,
    });
  }

  return events;
}

/** Known venue name -> coords mapping for venues that just have a name */
const VENUE_COORDS: Record<string, { lat: number; lng: number }> = {
  'lili latin dance':        { lat: 42.336527, lng: -71.047731 },
  'tambó salsa':             { lat: 42.365000, lng: -71.091000 },
  'tambo salsa':             { lat: 42.365000, lng: -71.091000 },
  'salsa y control dance studio': { lat: 42.352700, lng: -71.131800 },
  'arts at the armory':      { lat: 42.399600, lng: -71.098900 },
  'magazine beach, cambridge': { lat: 42.358400, lng: -71.114700 },
  'magazine beach':          { lat: 42.358400, lng: -71.114700 },
  'the dante alighieri society of massachusetts': { lat: 42.367900, lng: -71.088500 },
  'cantab lounge':           { lat: 42.365300, lng: -71.103100 },
  'pkl':                     { lat: 42.335200, lng: -71.046400 },
  'distillery gallery':      { lat: 42.340000, lng: -71.055000 },
  'club cafe boston':         { lat: 42.345300, lng: -71.072100 },
  'docks near the hatch memorial shell': { lat: 42.357256, lng: -71.073702 },
  'the anchor':              { lat: 42.3731772, lng: -71.0526574 },
  '1 shipyard park':         { lat: 42.3731772, lng: -71.0526574 },
  'shipyard park':           { lat: 42.3731772, lng: -71.0526574 },
};


async function nominatimQuery(query: string): Promise<{ lat: number; lng: number } | null> {
  try {
    const resp = await fetch(
      `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=1&countrycodes=us`,
      { headers: { 'User-Agent': 'boston-latin-dance-dev/0.1' } }
    );
    const data = await resp.json() as Array<{ lat: string; lon: string }>;
    if (data.length > 0) {
      return { lat: parseFloat(data[0].lat), lng: parseFloat(data[0].lon) };
    }
  } catch {
    // geocoding failure is non-fatal
  }
  return null;
}

function buildQueryVariants(location: string): string[] {
  const cleaned = location
    .replace(/#\w+\s*/g, '')
    .replace(/,\s*FL\s+\d+/i, '')
    .replace(/\s+-\s+\d+\w*\s+Floor/i, '')
    .trim();

  // Deduplicate repeated city/state segments (e.g. "charlestown, MA 02129, charlestown, MA")
  const parts = cleaned.split(',').map(p => p.trim());
  const seen = new Set<string>();
  const deduped: string[] = [];
  for (const part of parts) {
    const norm = part.toLowerCase().replace(/\d{5}(-\d{4})?/, '').trim();
    if (!seen.has(norm)) {
      seen.add(norm);
      deduped.push(part);
    }
  }
  const dedupedStr = deduped.join(', ');

  const base = dedupedStr.includes('MA') || dedupedStr.includes('Boston')
    ? dedupedStr
    : `${dedupedStr}, Boston, MA`;

  const variants = [base];

  // Try without leading street number (for parks/venues where "1 Park Name" confuses Nominatim)
  const noNumber = base.replace(/^\d+\s+/, '');
  if (noNumber !== base) variants.push(noNumber);

  return variants;
}

const GEOCODE_CACHE_PATH = resolve(import.meta.dirname ?? '.', '..', 'data', 'geocode-cache.json');

type GeoCache = Record<string, { lat: number; lng: number } | null>;

function loadGeoCache(): GeoCache {
  if (existsSync(GEOCODE_CACHE_PATH)) {
    try {
      return JSON.parse(readFileSync(GEOCODE_CACHE_PATH, 'utf-8'));
    } catch { /* ignore corrupt file */ }
  }
  return {};
}

function saveGeoCache(cache: GeoCache): void {
  writeFileSync(GEOCODE_CACHE_PATH, JSON.stringify(cache, null, 2));
}

const geoCache = loadGeoCache();

function lookupVenue(location: string): { lat: number; lng: number } | null {
  const lower = location.toLowerCase().trim();
  // Exact match first
  if (VENUE_COORDS[lower]) return VENUE_COORDS[lower];
  // Check if any known venue name appears within the location string
  for (const [venue, coords] of Object.entries(VENUE_COORDS)) {
    if (lower.includes(venue)) return coords;
  }
  return null;
}

async function geocodeAddress(location: string): Promise<{ lat: number; lng: number } | null> {
  if (!location) return null;

  const lower = location.toLowerCase().trim();
  const venueMatch = lookupVenue(location);
  if (venueMatch) return venueMatch;

  if (location.length < 5) return null;

  // Check cache (stores both hits and misses)
  if (lower in geoCache) return geoCache[lower];

  const variants = buildQueryVariants(location);

  for (const query of variants) {
    const result = await nominatimQuery(query);
    if (result && isNearBoston(result)) {
      geoCache[lower] = result;
      saveGeoCache(geoCache);
      return result;
    }
    if (result) {
      console.log(`  Rejected (too far): "${query}" -> ${result.lat}, ${result.lng} (${distKm(result.lat, result.lng, BOSTON.lat, BOSTON.lng).toFixed(1)}km)`);
    }
    await sleep(1100);
  }

  geoCache[lower] = null;
  saveGeoCache(geoCache);
  return null;
}

const BOSTON = { lat: 42.36, lng: -71.06 };
const MAX_DISTANCE_KM = 50;

function distKm(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const dlat = lat1 - lat2;
  const dlng = (lng1 - lng2) * Math.cos(lat1 * Math.PI / 180);
  return Math.sqrt(dlat * dlat + dlng * dlng) * 111;
}

function isNearBoston(coords: { lat: number; lng: number }): boolean {
  return distKm(coords.lat, coords.lng, BOSTON.lat, BOSTON.lng) <= MAX_DISTANCE_KM;
}

function sleep(ms: number): Promise<void> {
  return new Promise(r => setTimeout(r, ms));
}

async function main() {
  console.log('Fetching ICS feed...');
  const resp = await fetch(ICS_URL);
  if (!resp.ok) {
    console.error(`Failed to fetch ICS: ${resp.status} ${resp.statusText}`);
    process.exit(1);
  }
  const icsText = await resp.text();
  console.log(`Fetched ${icsText.length} bytes`);

  const allEvents = parseIcs(icsText);
  console.log(`Parsed ${allEvents.length} events`);

  // Only keep future events (from today onward)
  const now = Date.now();
  const events = allEvents.filter(e => new Date(e.startDate).getTime() >= now - 86400000);
  console.log(`  ${events.length} future events`);

  let geocoded = 0;
  for (const event of events) {
    const coords = await geocodeAddress(event.location);
    if (coords) {
      event.lat = coords.lat;
      event.lng = coords.lng;
      geocoded++;
      console.log(`  Geocoded: ${event.name} -> ${coords.lat}, ${coords.lng}`);
    } else if (event.location) {
      console.log(`  No coords: ${event.name} (location: "${event.location}")`);
    }
  }
  console.log(`Geocoded ${geocoded} events`);

  const withCoords = events.filter(e => e.lat && e.lng).length;
  const withoutCoords = events.length - withCoords;
  console.log(`  ${withCoords} with coordinates, ${withoutCoords} without`);

  const styleCounts: Record<string, number> = {};
  for (const e of events) {
    for (const s of e.styles) {
      styleCounts[s] = (styleCounts[s] || 0) + 1;
    }
  }
  console.log('  Style breakdown:', styleCounts);

  const outPath = resolve(import.meta.dirname ?? '.', '..', 'public', 'events.json');
  writeFileSync(outPath, JSON.stringify(events, null, 2));
  console.log(`Wrote ${events.length} events to ${outPath}`);

  // Also write to data/scraped/ for the merge pipeline
  const scrapedDir = resolve(import.meta.dirname ?? '.', '..', 'data', 'scraped');
  const scrapedPath = resolve(scrapedDir, 'beatrice-calendar.json');
  const tagged = events.map(e => ({ ...e, source: 'beatrice-calendar' }));
  writeFileSync(scrapedPath, JSON.stringify(tagged, null, 2));
  console.log(`Wrote ${tagged.length} events to ${scrapedPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
