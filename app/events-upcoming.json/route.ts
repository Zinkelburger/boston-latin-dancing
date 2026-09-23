import allEvents from '@/data/events-published.json';
import type { DanceEvent } from '@/types/event';

export const dynamic = 'force-static';

/**
 * The map's event list, exported as a static file (UPCOMING_EVENTS_PATH)
 * instead of being bundled into the client JS. Archived events stay out:
 * they are most of the published file, and only their own /event/<slug>
 * pages need them — those read the full file at build time and hand the map
 * the one event as a prop. Kept out of the bundle, a data refresh also no
 * longer changes the hash of a JS chunk every visitor must re-download.
 */
export function GET() {
  const upcoming = (allEvents as DanceEvent[]).filter(e => !e.archived);
  return Response.json(upcoming);
}
