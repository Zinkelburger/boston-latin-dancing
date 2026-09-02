import { describe, expect, it } from 'vitest';
import type { DanceEvent } from '@/types/event';
import { findActiveInstance } from '@/lib/search';

let seq = 0;
function ev(overrides: Partial<DanceEvent>): DanceEvent {
  seq += 1;
  return {
    id: `e${seq}`,
    slug: `slug-${seq}`,
    name: 'Event',
    startDate: '2026-09-05T23:00:00Z',
    endDate: '2026-09-06T03:00:00Z',
    dayOfWeek: 'Saturday',
    location: 'Havana Club, 288 Green St, Cambridge',
    lat: 42.36,
    lng: -71.1,
    description: '',
    url: null,
    styles: ['salsa'],
    cost: null,
    recurring: false,
    ...overrides,
  };
}

describe('findActiveInstance', () => {
  it('is undefined for an event that is not archived', () => {
    const live = ev({ name: 'Fuego y Candela' });
    expect(findActiveInstance(live, [live, ev({ name: 'Fuego y Candela' })])).toBeUndefined();
  });

  it('finds the live event with the same normalized name', () => {
    const old = ev({ name: 'Fuego y Candela!', archived: true });
    const next = ev({ name: 'fuego y candela' });
    expect(findActiveInstance(old, [old, next])).toBe(next);
  });

  it('accepts name containment only at the same venue', () => {
    const old = ev({ name: 'Fuego y Candela', archived: true });
    const sameVenue = ev({ name: 'Fuego y Candela 15-Year Anniversary' });
    const otherVenue = ev({
      name: 'Fuego y Candela 15-Year Anniversary',
      location: 'Somewhere Else, Boston',
    });
    expect(findActiveInstance(old, [old, otherVenue])).toBeUndefined();
    expect(findActiveInstance(old, [old, otherVenue, sameVenue])).toBe(sameVenue);
  });

  it('falls back to distinctive-word overlap at the same venue, ignoring dance stopwords', () => {
    const old = ev({ name: 'Noche Caliente Salsa Night', archived: true });
    const next = ev({ name: 'Noche Caliente Bachata Party' });
    expect(findActiveInstance(old, [old, next])).toBe(next);
  });

  it('never matches on word overlap alone across venues', () => {
    const old = ev({ name: 'Noche Caliente Salsa Night', archived: true });
    const elsewhere = ev({ name: 'Noche Caliente Bachata Party', location: 'Elsewhere, Boston' });
    expect(findActiveInstance(old, [old, elsewhere])).toBeUndefined();
  });

  it('does not match generic names that share only stopwords', () => {
    const old = ev({ name: 'Salsa Social', archived: true });
    const other = ev({ name: 'Bachata Social' });
    expect(findActiveInstance(old, [old, other])).toBeUndefined();
  });

  it('prefers a dated listing over a dateless search-only record', () => {
    const old = ev({ name: 'Fuego y Candela', archived: true });
    const hub = ev({ name: 'Fuego y Candela', searchOnly: true, startDate: '', endDate: '' });
    const dated = ev({ name: 'Fuego y Candela' });
    expect(findActiveInstance(old, [old, hub, dated])).toBe(dated);
    expect(findActiveInstance(old, [old, dated, hub])).toBe(dated);
    expect(findActiveInstance(old, [old, hub])).toBe(hub);
  });

  it('skips itself, archived events, and events without a page', () => {
    const old = ev({ name: 'Fuego y Candela', archived: true });
    const alsoOld = ev({ name: 'Fuego y Candela', archived: true });
    const noPage = ev({ name: 'Fuego y Candela', slug: undefined });
    expect(findActiveInstance(old, [old, alsoOld, noPage])).toBeUndefined();
  });
});
