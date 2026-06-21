'use client';

import { useState, useRef, useEffect, useMemo } from 'react';
import type { DanceEvent } from '@/types/event';
import { tokenize, searchAndRank } from '@/lib/search';
import { SearchResultsTable } from './EventTable';

type Props = {
  events: DanceEvent[];
  onSelectEvent: (event: DanceEvent) => void;
};

export default function SearchBar({ events, onSelectEvent }: Props) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const q = query.trim().toLowerCase();
  const tokens = useMemo(() => tokenize(q), [q]);

  const eventResults = useMemo(
    () => (q.length < 2 ? [] : searchAndRank(events, q, 8)),
    [q, events],
  );

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

  const handleSelect = (event: DanceEvent) => {
    onSelectEvent(event);
    setQuery('');
    setOpen(false);
    inputRef.current?.blur();
  };

  const showDropdown = open && q.length >= 2;

  return (
    <div ref={wrapperRef} className="absolute z-10 w-[25rem] max-w-[calc(100%-2rem)]" style={{ top: '1rem', left: '1rem' }}>
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
          placeholder="Search..."
          className="w-full rounded-full border border-gray-300 bg-white/95 text-sm text-gray-800 shadow-lg backdrop-blur-sm placeholder:text-gray-400 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-rose-400"
          style={{ padding: '0.75rem 1rem' }}
        />
      </div>

      {showDropdown && (
        <div className="mt-2 max-h-72 overflow-y-auto rounded-xl border border-gray-200 shadow-xl bg-white">
          {eventResults.length === 0 ? (
            <div className="px-4 py-3 text-sm text-gray-400">No results</div>
          ) : (
            <SearchResultsTable events={eventResults} onSelect={handleSelect} searchTokens={tokens} />
          )}
        </div>
      )}
    </div>
  );
}
