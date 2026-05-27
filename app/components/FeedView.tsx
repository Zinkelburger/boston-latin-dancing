'use client';

import { useMemo, useState, type ReactNode } from 'react';
import type { DanceEvent, DayOfWeek } from '@/types/event';
import { STYLE_LABELS, STYLE_PILL_CLASS, SITE_URL } from '@/lib/constants';
import { stripHtml } from '@/lib/strip-html';
import { getRecurrenceLabel, recurrencesInRange, isDateOnlyEvent } from '@/lib/recurrences';
import ShareButton from './ShareButton';

const DAY_NAMES: DayOfWeek[] = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

type FeedEntry = {
  event: DanceEvent;
  displayDate: string;
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  });
}

function dateKey(iso: string): string {
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

type DateGroup = { key: string; label: string; entries: FeedEntry[] };

function expandAndGroup(
  events: DanceEvent[],
  selectedDays: DayOfWeek[],
  fromMs: number,
  toMs: number,
): DateGroup[] {
  const entries: FeedEntry[] = [];

  for (const event of events) {
    if (event.recurrences && event.recurrences.length > 0) {
      for (const recDate of recurrencesInRange(event.recurrences, fromMs, toMs)) {
        const d = new Date(recDate);
        const day = DAY_NAMES[d.getDay()];
        if (selectedDays.length > 0 && !selectedDays.includes(day)) continue;
        entries.push({ event, displayDate: recDate });
      }
    } else {
      const d = new Date(event.startDate);
      const day = DAY_NAMES[d.getDay()];
      if (selectedDays.length > 0 && !selectedDays.includes(day)) continue;
      entries.push({ event, displayDate: event.startDate });
    }
  }

  entries.sort(
    (a, b) => new Date(a.displayDate).getTime() - new Date(b.displayDate).getTime(),
  );

  const groups = new Map<string, FeedEntry[]>();
  for (const entry of entries) {
    const key = dateKey(entry.displayDate);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(entry);
  }

  return [...groups.entries()].map(([key, evts]) => ({
    key,
    label: formatDate(evts[0].displayDate),
    entries: evts,
  }));
}

function editDistance(a: string, b: string): number {
  const m = a.length, n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  let prev = Array.from({ length: n + 1 }, (_, i) => i);
  for (let i = 1; i <= m; i++) {
    const curr = [i];
    for (let j = 1; j <= n; j++) {
      curr[j] = a[i - 1] === b[j - 1]
        ? prev[j - 1]
        : 1 + Math.min(prev[j - 1], prev[j], curr[j - 1]);
    }
    prev = curr;
  }
  return prev[n];
}

function substringMatch(tok: string, field: string): boolean {
  return field.includes(tok);
}

function fuzzyWordMatch(tok: string, field: string): boolean {
  if (tok.length < 3) return false;
  const maxDist = tok.length <= 4 ? 1 : 2;
  const words = field.split(/[^a-z0-9]+/);
  for (const word of words) {
    if (word.length === 0) continue;
    if (word.startsWith(tok) || tok.startsWith(word)) return true;
    if (Math.abs(word.length - tok.length) <= maxDist && editDistance(tok, word) <= maxDist) return true;
  }
  return false;
}

type Token = { text: string; exact: boolean };

function parseTokens(raw: string): Token[] {
  const tokens: Token[] = [];
  const parts = raw.toLowerCase().split(/(\s+)/);
  for (let i = 0; i < parts.length; i++) {
    const word = parts[i].trim();
    if (!word) continue;
    const followedBySpace = i + 1 < parts.length && /\s/.test(parts[i + 1]);
    tokens.push({ text: word, exact: followedBySpace });
  }
  return tokens;
}

function tokenMatchesField(tok: Token, field: string): boolean {
  if (tok.exact) {
    const re = new RegExp(`\\b${tok.text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`);
    return re.test(field);
  }
  return substringMatch(tok.text, field) || fuzzyWordMatch(tok.text, field);
}

function eventMatchesQuery(event: DanceEvent, query: string): boolean {
  const tokens = parseTokens(query);
  if (tokens.length === 0) return true;

  const name = event.name.toLowerCase();
  const loc = (event.location ?? '').toLowerCase();
  const desc = stripHtml(event.description).toLowerCase();
  const styles = event.styles.map(s => STYLE_LABELS[s].toLowerCase()).join(' ');

  for (const tok of tokens) {
    if (
      !tokenMatchesField(tok, name) &&
      !tokenMatchesField(tok, loc) &&
      !tokenMatchesField(tok, desc) &&
      !tokenMatchesField(tok, styles)
    ) return false;
  }
  return true;
}

function highlightText(text: string, tokens: string[]): ReactNode {
  if (tokens.length === 0) return text;

  const lower = text.toLowerCase();
  const marks: boolean[] = new Array(text.length).fill(false);

  for (const tok of tokens) {
    let start = 0;
    while (true) {
      const idx = lower.indexOf(tok, start);
      if (idx === -1) break;
      for (let i = idx; i < idx + tok.length; i++) marks[i] = true;
      start = idx + 1;
    }
  }

  if (!marks.some(Boolean)) return text;

  const parts: ReactNode[] = [];
  let i = 0;
  while (i < text.length) {
    const marked = marks[i];
    let j = i;
    while (j < text.length && marks[j] === marked) j++;
    const slice = text.slice(i, j);
    parts.push(
      marked
        ? <mark key={i} className="feed-highlight">{slice}</mark>
        : slice,
    );
    i = j;
  }
  return <>{parts}</>;
}

type Props = {
  events: DanceEvent[];
  selectedDays: DayOfWeek[];
  fromMs: number;
  toMs: number;
  onSelectEvent: (event: DanceEvent, displayDate?: string) => void;
};

export default function FeedView({ events, selectedDays, fromMs, toMs, onSelectEvent }: Props) {
  const [search, setSearch] = useState('');
  const trimmed = search.trim();

  const searchTokens = useMemo(
    () => trimmed.toLowerCase().split(/\s+/).filter(Boolean),
    [trimmed],
  );

  const filtered = useMemo(
    () => trimmed ? events.filter(e => eventMatchesQuery(e, trimmed)) : events,
    [events, trimmed],
  );

  const grouped = useMemo(
    () => expandAndGroup(filtered, selectedDays, fromMs, toMs),
    [filtered, selectedDays, fromMs, toMs],
  );
  const totalEntries = useMemo(() => grouped.reduce((n, g) => n + g.entries.length, 0), [grouped]);

  return (
    <div className="feed-view">
      <div className="feed-header">
        <div className="feed-search-wrap">
          <svg className="feed-search-icon" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path fillRule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z" clipRule="evenodd" />
          </svg>
          <input
            type="text"
            className="feed-search"
            placeholder="Search..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button
              className="feed-search-clear"
              onClick={() => setSearch('')}
              aria-label="Clear search"
            >
              ×
            </button>
          )}
        </div>
      </div>

      <div className="feed-scroll">
        {grouped.map(group => (
          <div key={group.key} className="feed-group">
            <div className="feed-group-label">{group.label}</div>
            {group.entries.map(entry => (
              <FeedCard
                key={`${entry.event.id}-${entry.displayDate}`}
                event={entry.event}
                displayDate={entry.displayDate}
                onSelect={() => onSelectEvent(entry.event, entry.displayDate)}
                searchTokens={searchTokens}
              />
            ))}
          </div>
        ))}

        {grouped.length === 0 && (
          <div className="feed-empty">
            {trimmed ? 'No events match your search.' : 'No events match your filters.'}
          </div>
        )}
      </div>
    </div>
  );
}

function scheduleTimeForDate(event: DanceEvent, displayDate: string): string | null {
  const d = new Date(displayDate);
  const day = DAY_NAMES[d.getDay()];
  const entry = event.schedule?.find(s => s.dayOfWeek === day);
  return entry?.time ?? null;
}

function excerptAround(text: string, tokens: string[], maxLen = 120): string {
  if (text.length <= maxLen) return text;

  const lower = text.toLowerCase();
  let earliest = -1;
  for (const tok of tokens) {
    const idx = lower.indexOf(tok);
    if (idx !== -1 && (earliest === -1 || idx < earliest)) earliest = idx;
  }

  if (earliest === -1 || earliest <= maxLen / 2) {
    const end = text.lastIndexOf(' ', maxLen);
    return text.slice(0, end > 0 ? end : maxLen) + '...';
  }

  const start = Math.max(0, earliest - Math.floor(maxLen / 3));
  const wordStart = start === 0 ? 0 : text.indexOf(' ', start) + 1;
  const end = Math.min(text.length, wordStart + maxLen);
  const wordEnd = end >= text.length ? text.length : text.lastIndexOf(' ', end);
  const slice = text.slice(wordStart, wordEnd > wordStart ? wordEnd : end);
  return (wordStart > 0 ? '...' : '') + slice + (wordEnd < text.length ? '...' : '');
}

function FeedCard({
  event,
  displayDate,
  onSelect,
  searchTokens,
}: {
  event: DanceEvent;
  displayDate: string;
  onSelect: () => void;
  searchTokens: string[];
}) {
  const cleanDesc = stripHtml(event.description);
  const shortDesc = searchTokens.length > 0
    ? excerptAround(cleanDesc, searchTokens)
    : cleanDesc.length > 120
      ? cleanDesc.slice(0, cleanDesc.lastIndexOf(' ', 120)) + '...'
      : cleanDesc;

  const shareUrl = event.slug ? `${SITE_URL}/event/${event.slug}` : '';
  const scheduleTime = event.recurrences ? scheduleTimeForDate(event, displayDate) : null;
  const recurrenceLabel = getRecurrenceLabel(event);

  const hl = (text: string) => highlightText(text, searchTokens);

  return (
    <div role="button" tabIndex={0} className="feed-card" onClick={onSelect} onKeyDown={e => { if (e.key === 'Enter') onSelect(); }}>
      <div className="feed-card-top">
        <div className="feed-card-pills">
          {event.styles.map(style => (
            <span
              key={style}
              className={`pretty-pill ${STYLE_PILL_CLASS[style]} text-xs`}
            >
              {hl(STYLE_LABELS[style])}
            </span>
          ))}
          {event.recurring && !recurrenceLabel && (
            <span className="pretty-pill pretty-pill-neutral text-xs">
              Recurring
            </span>
          )}
        </div>
        {shareUrl && (
          <div onClick={e => e.stopPropagation()}>
            <ShareButton
              url={shareUrl}
              title={event.name}
              className="shrink-0 text-xs"
            />
          </div>
        )}
      </div>

      <h3 className="feed-card-title">{hl(event.name)}</h3>

      {recurrenceLabel && !(event.schedule && event.schedule.length > 0) && (
        <span className="pretty-pill pretty-pill-sky text-xs" style={{ alignSelf: 'flex-start' }}>
          {recurrenceLabel}
        </span>
      )}

      <div className="feed-card-meta">
        <span className="feed-card-date">
          {event.schedule && event.schedule.length > 0
            ? formatDate(displayDate)
            : scheduleTime
              ? `${formatDate(displayDate)} \u00B7 ${scheduleTime}`
              : isDateOnlyEvent(event.startDate, event.endDate)
                ? formatDate(displayDate)
                : `${formatDate(displayDate)} \u00B7 ${formatTime(event.startDate)} – ${formatTime(event.endDate)}`
          }
        </span>
      </div>

      {event.location && (
        <div className="feed-card-location">{hl(event.location)}</div>
      )}

      {event.cost && <div className="feed-card-cost">{event.cost}</div>}

      {shortDesc && (
        <p className="feed-card-desc">{hl(shortDesc)}</p>
      )}
    </div>
  );
}
