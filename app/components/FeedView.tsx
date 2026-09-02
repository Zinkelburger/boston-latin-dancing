'use client';

import { useMemo, useState } from 'react';
import type { DanceEvent, DayOfWeek } from '@/types/event';
import { STYLE_LABELS, STYLE_PILL_CLASS, SITE_URL } from '@/lib/constants';
import { tokenize, matchEvent } from '@/lib/search';
import { cleanDisplayText } from '@/lib/display-text';
import { excerptAround, highlightText } from '@/lib/highlight';
import {
  dayOfWeekFromIso,
  formatRecurrenceDate,
  formatRecurrenceTime,
  getRecurrenceLabel,
  isDateOnlyEvent,
  occurrenceMatchesDays,
  recurringWhenLabel,
  occurrencesInRange,
  shouldShowNextOccurrence,
} from '@/lib/recurrences';
import ShareButton from './ShareButton';
import MetaRow from './MetaRow';
import FilterBar from './FilterBar';
import type { FilterControlsProps } from './useEventFilters';

type FeedEntry = {
  event: DanceEvent;
  displayDate: string;
};

// All labels/grouping are pinned to Boston time: these are Boston events, and
// a visitor (or server) in another timezone must see the same dates as the
// Boston-pinned day filters, or late-night events land under the wrong day.
function dateKey(iso: string): string {
  // en-CA yields YYYY-MM-DD directly.
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
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
    for (const occ of occurrencesInRange(event, fromMs, toMs)) {
      if (!occurrenceMatchesDays(occ, selectedDays)) continue;
      entries.push({ event, displayDate: occ });
    }
  }

  entries.sort((a, b) => new Date(a.displayDate).getTime() - new Date(b.displayDate).getTime());

  const groups = new Map<string, FeedEntry[]>();
  for (const entry of entries) {
    const key = dateKey(entry.displayDate);
    const group = groups.get(key);
    if (group) group.push(entry);
    else groups.set(key, [entry]);
  }

  return [...groups.entries()].map(([key, evts]) => ({
    key,
    label: formatRecurrenceDate(evts[0].displayDate),
    entries: evts,
  }));
}

type Props = {
  /** Filter state + handlers from useEventFilters, shared with the map's bar. */
  controls: FilterControlsProps;
  events: DanceEvent[];
  fromMs: number;
  toMs: number;
  onSelectEvent: (event: DanceEvent, displayDate?: string) => void;
  onViewModeToggle: () => void;
};

/** Feed cards get a longer description window than a search result row. */
const CARD_DESC_LEN = 120;

export default function FeedView({
  controls,
  events,
  fromMs,
  toMs,
  onSelectEvent,
  onViewModeToggle,
}: Props) {
  const { selectedDays } = controls;
  const [search, setSearch] = useState('');

  const trimmed = search.trim();

  const searchTokens = useMemo(() => tokenize(trimmed), [trimmed]);

  const filtered = useMemo(
    () => (trimmed ? events.filter(e => matchEvent(e, search)) : events),
    [events, search, trimmed],
  );

  const grouped = useMemo(
    () => expandAndGroup(filtered, selectedDays, fromMs, toMs),
    [filtered, selectedDays, fromMs, toMs],
  );

  return (
    <div className="feed-view">
      {/* Left column carries search + every filter row; the right column pins
          the close button to the top and Map to the bottom-most row. */}
      {/* Search sits with the close button; the filter bar underneath is the
          same component the map uses, so phone and desktop stay in step. */}
      <div className="feed-header">
        <div className="feed-search-row">
          <div className="feed-search-wrap">
            <svg
              className="feed-search-icon"
              viewBox="0 0 20 20"
              fill="currentColor"
              aria-hidden="true"
            >
              <path
                fillRule="evenodd"
                d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z"
                clipRule="evenodd"
              />
            </svg>
            <input
              type="text"
              className="feed-search"
              placeholder="Search events, venues, styles..."
              aria-label="Search events, venues, styles"
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
          <button onClick={onViewModeToggle} className="feed-close-btn" aria-label="Close feed">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <FilterBar controls={controls} viewMode="feed" onViewModeToggle={onViewModeToggle} />
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
  const day = dayOfWeekFromIso(displayDate);
  const entry = event.schedule?.find(s => s.dayOfWeek === day);
  return entry?.time ?? null;
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
  const cleanDesc = cleanDisplayText(event.description);
  const shortDesc =
    searchTokens.length > 0
      ? excerptAround(cleanDesc, searchTokens, { maxLen: CARD_DESC_LEN, ellipsis: '...' })
      : cleanDesc.length > CARD_DESC_LEN
        ? cleanDesc.slice(0, cleanDesc.lastIndexOf(' ', CARD_DESC_LEN)) + '...'
        : cleanDesc;

  const shareUrl = event.slug ? `${SITE_URL}/event/${event.slug}` : '';
  const scheduleTime = event.recurrences ? scheduleTimeForDate(event, displayDate) : null;
  const recurrenceLabel = getRecurrenceLabel(event);

  const hl = (text: string) => highlightText(text, searchTokens);

  const dateText =
    event.schedule && event.schedule.length > 0
      ? formatRecurrenceDate(displayDate)
      : scheduleTime
        ? `${formatRecurrenceDate(displayDate)} · ${scheduleTime}`
        : isDateOnlyEvent(event.startDate, event.endDate)
          ? formatRecurrenceDate(displayDate)
          : `${formatRecurrenceDate(displayDate)} · ${formatRecurrenceTime(event.startDate)} – ${formatRecurrenceTime(event.endDate)}`;

  return (
    <div
      role="button"
      tabIndex={0}
      className="feed-card"
      onClick={onSelect}
      onKeyDown={e => {
        if (e.key === 'Enter') onSelect();
      }}
    >
      {/* Name first — the pills only mean something once you know what they
          are describing. Styles next, then status, then the facts. */}
      <div className="feed-card-top">
        <h3 className="feed-card-title">
          {shareUrl ? (
            <a href={`/event/${event.slug}`} onClick={e => e.stopPropagation()}>
              {hl(event.name)}
            </a>
          ) : (
            hl(event.name)
          )}
        </h3>
        {shareUrl && (
          <div onClick={e => e.stopPropagation()}>
            <ShareButton url={shareUrl} title={event.name} className="shrink-0 text-xs" />
          </div>
        )}
      </div>

      {event.styles.length > 0 && (
        <div className="feed-card-pills">
          {event.styles.map(style => (
            <span key={style} className={`pretty-pill ${STYLE_PILL_CLASS[style]} text-xs`}>
              {hl(STYLE_LABELS[style])}
            </span>
          ))}
        </div>
      )}

      {(recurrenceLabel || event.recurring || event.special || event.nextDateApproximate) && (
        <div className="feed-card-pills">
          {recurrenceLabel && (
            <span className="pretty-pill pretty-pill-sky text-xs">
              {shouldShowNextOccurrence(event) ? recurringWhenLabel(event) : recurrenceLabel}
            </span>
          )}
          {event.recurring && !recurrenceLabel && (
            <span className="pretty-pill pretty-pill-neutral text-xs">Recurring</span>
          )}
          {event.special && (
            <span className="pretty-pill pretty-pill-fuchsia text-xs">Big Event</span>
          )}
          {event.nextDateApproximate && (
            <span className="pretty-pill pretty-pill-amber text-xs">Date unconfirmed</span>
          )}
        </div>
      )}

      <div className="feed-card-facts">
        <MetaRow icon="calendar">{dateText}</MetaRow>
        {event.location && <MetaRow icon="pin">{hl(event.location)}</MetaRow>}
        {event.cost && <MetaRow icon="cost">{event.cost}</MetaRow>}
      </div>

      {shortDesc && <p className="feed-card-desc">{hl(shortDesc)}</p>}
    </div>
  );
}
