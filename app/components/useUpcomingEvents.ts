'use client';

import { useEffect, useState } from 'react';
import { preload } from 'react-dom';
import type { DanceEvent } from '@/types/event';
import { UPCOMING_EVENTS_PATH } from '@/lib/constants';

const NONE: DanceEvent[] = [];

/** One request per page load, shared by every caller. */
let request: Promise<DanceEvent[]> | null = null;

function fetchUpcoming(): Promise<DanceEvent[]> {
  request ??= fetch(UPCOMING_EVENTS_PATH).then(res => {
    if (!res.ok) throw new Error(`${UPCOMING_EVENTS_PATH}: HTTP ${res.status}`);
    return res.json() as Promise<DanceEvent[]>;
  });
  return request;
}

/**
 * Every published event that has not passed (see app/events-upcoming.json),
 * or an empty list until it arrives. The preload lands in the server-rendered
 * <head>, so the download runs alongside the JS chunks rather than starting
 * after hydration — by the time the map can draw pins the data is usually in.
 */
export function useUpcomingEvents(): DanceEvent[] {
  preload(UPCOMING_EVENTS_PATH, { as: 'fetch', crossOrigin: 'anonymous' });
  const [events, setEvents] = useState<DanceEvent[]>(NONE);

  useEffect(() => {
    let live = true;
    fetchUpcoming().then(
      list => {
        if (live) setEvents(list);
      },
      err => {
        // Let the next mount try again rather than caching the failure.
        request = null;
        console.error('Could not load events', err);
      },
    );
    return () => {
      live = false;
    };
  }, []);

  return events;
}
