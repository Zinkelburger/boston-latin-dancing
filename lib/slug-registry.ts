import registry from '@/data/slug-registry.json';

/**
 * Every event URL we have ever published, and where it should land today.
 *
 * Slugs are minted from the event name, so a dedup merge or a re-scrape can
 * retire a URL that Google has already indexed. Those URLs must keep working:
 * a search result that 404s is worse than one that never existed. The registry
 * (maintained by scripts/slug_registry.py) records the retired ones and what
 * replaced them, so the static export can ship a page for each.
 */

export type SlugStatus = 'live' | 'alias' | 'ended';

interface RegistryEntry {
  id?: string;
  name?: string;
  location?: string;
  startDate?: string;
  status: SlugStatus;
  target: string | null;
  reason?: string;
}

const entries = (registry as { entries: Record<string, RegistryEntry> }).entries;

/** Slugs that no longer have an event but must still resolve. */
export function retiredSlugs(): string[] {
  return Object.keys(entries).filter(s => entries[s].status !== 'live');
}

export function lookupSlug(slug: string): RegistryEntry | undefined {
  return entries[slug];
}

/** The slug a retired URL should send a visitor to, if there is one. */
export function redirectTarget(slug: string): string | null {
  const entry = entries[slug];
  if (!entry || entry.status !== 'alias') return null;
  return entry.target;
}

/** The name this URL was published under — used so an ended page can still
 *  say which event it was, long after the event data itself is gone. */
export function retiredName(slug: string): string | undefined {
  return entries[slug]?.name;
}
