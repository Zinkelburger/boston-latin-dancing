'use client';

import { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import type { DanceEvent } from '@/types/event';

type NominatimResult = {
  place_id: number;
  display_name: string;
  lat: string;
  lon: string;
};

type Props = {
  events: DanceEvent[];
  onSelectEvent: (event: DanceEvent) => void;
  onFlyTo: (lat: number, lng: number) => void;
};

export default function SearchBar({ events, onSelectEvent, onFlyTo }: Props) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [places, setPlaces] = useState<NominatimResult[]>([]);
  const [loadingPlaces, setLoadingPlaces] = useState(false);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const eventResults = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length < 2) return [];

    const tokens = q.split(/\s+/).filter(Boolean);

    type Scored = { event: DanceEvent; score: number };
    const scored: Scored[] = [];

    for (const e of events) {
      if (!e.lat || !e.lng) continue;
      const name = e.name.toLowerCase();
      const loc = e.location.toLowerCase();
      const styles = e.styles.join(' ');
      const desc = e.description.toLowerCase();
      const dayLower = e.dayOfWeek.toLowerCase();

      let score = 0;
      let allMatch = true;
      for (const tok of tokens) {
        const inName = name.includes(tok);
        const inLoc = loc.includes(tok);
        const inStyle = styles.includes(tok);
        const inDay = dayLower.startsWith(tok);
        const inDesc = desc.includes(tok);

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
      if (allMatch && score > 0) scored.push({ event: e, score });
    }

    return scored
      .sort((a, b) => b.score - a.score)
      .slice(0, 8)
      .map(s => s.event);
  }, [query, events]);

  const fetchPlaces = useCallback((q: string) => {
    abortRef.current?.abort();
    if (q.trim().length < 3) {
      setPlaces([]);
      setLoadingPlaces(false);
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setLoadingPlaces(true);
    fetch(
      `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(q)}&format=json&limit=3&addressdetails=0`,
      { signal: controller.signal },
    )
      .then(r => r.json() as Promise<NominatimResult[]>)
      .then(data => {
        setPlaces(data);
        setLoadingPlaces(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoadingPlaces(false);
      });
  }, []);

  useEffect(() => {
    const id = setTimeout(() => fetchPlaces(query), 350);
    return () => clearTimeout(id);
  }, [query, fetchPlaces]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  const handleSelect = (cb: () => void) => {
    cb();
    setQuery('');
    setOpen(false);
    setPlaces([]);
    inputRef.current?.blur();
  };

  const hasResults = eventResults.length > 0 || places.length > 0;
  const showDropdown = open && query.trim().length >= 2;

  return (
    <div ref={wrapperRef} className="absolute z-10 w-[16rem] max-w-[calc(100%-2rem)]" style={{ top: '1rem', left: '1rem' }}>
      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={e => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder="Search events or places..."
          className="w-full rounded-full border border-gray-300 bg-white/95 text-sm text-gray-800 shadow-lg backdrop-blur-sm placeholder:text-gray-400 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-rose-400"
          style={{ padding: '0.75rem 1rem' }}
        />
      </div>

      {showDropdown && (
        <div className="mt-2 max-h-72 overflow-y-auto rounded-xl border border-gray-200 shadow-xl bg-white">
          {!hasResults && !loadingPlaces && (
            <div className="px-4 py-3 text-sm text-gray-400">No results</div>
          )}

          {eventResults.length > 0 && (
            <div>
              <div className="px-4 pt-3 pb-1 text-xs font-semibold uppercase tracking-wider text-gray-400">
                Events
              </div>
              {eventResults.map(event => {
                const d = new Date(event.startDate);
                const dateLabel = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                return (
                  <button
                    key={event.id}
                    onClick={() => handleSelect(() => onSelectEvent(event))}
                    className="flex w-full items-center gap-2 text-left transition-colors hover:bg-rose-50 px-4 py-2"
                  >
                    <span className="inline-block w-2 h-2 rounded-full shrink-0" style={{ background: '#e11d48' }} />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm truncate">{event.name}</div>
                      <div className="text-xs text-gray-400 truncate">
                        {dateLabel} · {event.styles.join(', ')} · {event.location || 'TBA'}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}

          {places.length > 0 && (
            <div>
              {eventResults.length > 0 && <div className="border-t border-gray-100" />}
              <div className="px-4 pt-3 pb-1 text-xs font-semibold uppercase tracking-wider text-gray-400">
                Places
              </div>
              {places.map(p => (
                <button
                  key={p.place_id}
                  onClick={() => handleSelect(() => onFlyTo(parseFloat(p.lat), parseFloat(p.lon)))}
                  className="flex w-full items-center gap-2 text-left transition-colors hover:bg-blue-50 px-4 py-2"
                >
                  <svg className="shrink-0 text-gray-400" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
                    <circle cx="12" cy="10" r="3" />
                  </svg>
                  <span className="text-sm truncate">{p.display_name}</span>
                </button>
              ))}
            </div>
          )}

          {loadingPlaces && places.length === 0 && eventResults.length === 0 && (
            <div className="px-4 py-3 text-sm text-gray-400">Searching...</div>
          )}
        </div>
      )}
    </div>
  );
}
