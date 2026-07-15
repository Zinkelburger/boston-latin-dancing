import type { DanceEvent } from '@/types/event';
import { STYLE_LABELS } from './constants';
import { stripHtml } from './strip-html';

/** Split a query into lowercased, non-empty tokens. */
export function tokenize(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

/** Lowercase, strip punctuation, collapse whitespace — for name-equality matching. */
export function normalizeEventName(name: string): string {
  return name.toLowerCase().replace(/[^\w\s]/g, '').replace(/\s+/g, ' ').trim();
}

function venueKey(location: string | null | undefined): string {
  return (location || '').split(',')[0].toLowerCase().trim();
}

/**
 * Whether `e` is an instance of a search-only venue record's series. A venue
 * record's source IS the organizer's own feed, so a shared organizer ties
 * special-edition names ("Fuego y Candela 15-Year Anniversary…") back to the
 * series even when the name diverges. Falls back to name matching, so it's
 * safe to call with any event as `record`.
 */
export function isSeriesInstance(record: DanceEvent, e: DanceEvent): boolean {
  if (isSameSeries(record, e)) return true;
  return Boolean(record.searchOnly && record.organizer && record.organizer === e.organizer);
}

/** Same-series test: exact normalized name, or name containment at the same venue. */
export function isSameSeries(a: DanceEvent, b: DanceEvent): boolean {
  const na = normalizeEventName(a.name);
  const nb = normalizeEventName(b.name);
  if (na === nb) return true;
  if (na.includes(nb) || nb.includes(na)) {
    const va = venueKey(a.location);
    const vb = venueKey(b.location);
    return Boolean(va && vb && va === vb);
  }
  return false;
}

type Fields = { name: string; loc: string; desc: string; styles: string; day: string };

/** Lowercased searchable fields for an event. `styles` includes both the raw
 *  style ids and their display labels so either spelling matches. */
function eventFields(event: DanceEvent): Fields {
  return {
    name: event.name.toLowerCase(),
    loc: (event.location ?? '').toLowerCase(),
    desc: stripHtml(event.description).toLowerCase(),
    styles: [...event.styles, ...event.styles.map(s => STYLE_LABELS[s])].join(' ').toLowerCase(),
    day: event.dayOfWeek.toLowerCase(),
  };
}

/**
 * Whether an event matches a free-text query. A token followed by whitespace in
 * the query must match as a whole word; a trailing (still-being-typed) token may
 * match as a substring. Used by the feed's incremental filter.
 */
export function matchEvent(event: DanceEvent, query: string): boolean {
  const lower = query.toLowerCase();
  const parts = lower.split(/(\s+)/);
  const tokens: { text: string; exact: boolean }[] = [];
  for (let i = 0; i < parts.length; i++) {
    const word = parts[i].trim();
    if (!word) continue;
    const followedBySpace = i + 1 < parts.length && /\s/.test(parts[i + 1]);
    tokens.push({ text: word, exact: followedBySpace });
  }
  if (tokens.length === 0) return true;

  const f = eventFields(event);
  const haystack = `${f.name} ${f.loc} ${f.desc} ${f.styles}`;

  return tokens.every(tok => {
    if (tok.exact) {
      const re = new RegExp(`(?:^|\\W)${tok.text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:\\W|$)`);
      return re.test(haystack);
    }
    return haystack.includes(tok.text);
  });
}

/**
 * Rank events against a query by weighted field matches (name > location >
 * style/day > description), returning the best `limit` matches. Every token must
 * match at least one field. Used by the map search dropdown.
 */
export function searchAndRank(events: DanceEvent[], query: string, limit: number): DanceEvent[] {
  const tokens = tokenize(query);
  if (tokens.length === 0) return [];

  const scored: { event: DanceEvent; score: number }[] = [];
  for (const event of events) {
    const f = eventFields(event);
    let score = 0;
    let allMatch = true;
    for (const tok of tokens) {
      const inName = f.name.includes(tok);
      const inLoc = f.loc.includes(tok);
      const inStyle = f.styles.includes(tok);
      const inDay = f.day.startsWith(tok);
      const inDesc = f.desc.includes(tok);
      if (!inName && !inLoc && !inStyle && !inDay && !inDesc) {
        allMatch = false;
        break;
      }
      if (inName) score += 10;
      if (inLoc) score += 5;
      if (inStyle) score += 3;
      if (inDay) score += 3;
      if (inDesc) score += 1;
    }
    if (allMatch && score > 0) scored.push({ event, score });
  }

  return scored
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map(s => s.event);
}
