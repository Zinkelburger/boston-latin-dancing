'use client';

import { useRef, useState, useMemo, useCallback, useEffect } from 'react';
import dynamic from 'next/dynamic';
import type { MapRef } from 'react-map-gl/maplibre';
import type { MapLayerMouseEvent } from 'maplibre-gl';
import type { Feature, FeatureCollection, Point } from 'geojson';

import allEvents from '@/data/events-published.json';
import type { DanceEvent, DanceStyle } from '@/types/event';
import { STYLE_COLORS } from '@/lib/constants';
import FilterBar from './FilterBar';
import EventPopup from './EventPopup';
import SearchBar from './SearchBar';
import FeedView from './FeedView';
import { useEventFilters } from './useEventFilters';
import { isSeriesInstance, normalizeEventName } from '@/lib/search';
import type { MapViewState } from './EventMap';
import PinOverlay, { type PinProps } from './PinOverlay';

// maplibre-gl is ~1 MB (270 kB gzipped) and a deep-linked visitor does not
// need any of it to read the event. Loading it lazily keeps it out of the
// chunks React needs to hydrate this tree, so the popup opens while the map
// library is still on the wire. `ssr: false` because maplibre touches
// `window` on import.
const EventMap = dynamic(() => import('./EventMap'), {
  ssr: false,
  // PinOverlay paints the ground and the pins while this is pending.
  loading: () => null,
});

const DEFAULT_VIEW: MapViewState = { longitude: -71.08, latitude: 42.36, zoom: 11 };

/** Archived events and search-only venue records: shown as a translucent dot
 *  when opened, never a normal pin, and exempt from filters. */
function isGhostEvent(event: DanceEvent | null | undefined): boolean {
  return Boolean(event && (event.archived || event.searchOnly));
}

type MarkerProps = PinProps;
type MarkerFeature = Feature<Point, MarkerProps>;
type MarkerCollection = FeatureCollection<Point, MarkerProps>;

/** An event with coordinates — the only kind that can be a pin. */
type MappableEvent = DanceEvent & { lat: number; lng: number };

function isMappable(event: DanceEvent): event is MappableEvent {
  return event.lat != null && event.lng != null;
}

function primaryColor(event: { styles: DanceStyle[] }): string {
  const primary = event.styles[0];
  return (primary && STYLE_COLORS[primary]) || STYLE_COLORS.other;
}

/** History state written by the popup so Back/Forward can close and reopen it. */
type PopupHistoryState = { popup?: boolean } | null;

function slugFromHash(hash: string): string | null {
  const match = hash.match(/^#event=(.+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

/** The current URL without its hash — what closing the popup leaves behind. */
function urlWithoutHash(): string {
  return window.location.pathname + window.location.search;
}

function staggerCoordinates(
  items: { id: string; lat: number; lng: number }[],
): Map<string, [number, number]> {
  const groups = new Map<string, string[]>();
  for (const e of items) {
    const key = `${e.lat.toFixed(5)},${e.lng.toFixed(5)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(e.id);
  }

  const offsets = new Map<string, [number, number]>();

  for (const [, ids] of groups) {
    if (ids.length <= 1) continue;
    const OFFSET = 0.00035;
    for (let i = 0; i < ids.length; i++) {
      const angle = (2 * Math.PI * i) / ids.length;
      const dlng = OFFSET * Math.cos(angle);
      const dlat = OFFSET * Math.sin(angle);
      offsets.set(ids[i], [dlng, dlat]);
    }
  }

  return offsets;
}

export default function MapView({ initialEventSlug }: { initialEventSlug?: string } = {}) {
  const mapRef = useRef<MapRef>(null);
  const allEventsTyped = useMemo(() => allEvents as DanceEvent[], []);
  const events = useMemo(
    () => allEventsTyped.filter(e => !e.archived && !e.searchOnly),
    [allEventsTyped],
  );

  const { controls, applyFilters, effectiveFromMs, effectiveToMs, ensureEventVisible } =
    useEventFilters();

  const [activeEvent, setActiveEvent] = useState<DanceEvent | null>(null);
  const [activeDisplayDate, setActiveDisplayDate] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'map' | 'feed'>('map');
  const [highlightedEvent, setHighlightedEvent] = useState<DanceEvent | null>(null);
  /** True once the (re)mounted map has fired its `load` event and is safe to drive. */
  const [mapReady, setMapReady] = useState(false);

  /** Event to fly to once the map is ready (set by feed selection). */
  const pendingFlyToRef = useRef<DanceEvent | null>(null);
  /**
   * Camera the map mounts with. A deep-link sets this to the venue before the
   * map chunk has even arrived, so the first frame is already on the pin —
   * no city-wide tiles, no 1.2 s flight, nothing for the popup to wait on.
   */
  const [initialView, setInitialView] = useState<MapViewState>(DEFAULT_VIEW);

  const flyToEvent = useCallback((event: DanceEvent) => {
    if (event.lat == null || event.lng == null) return;
    mapRef.current?.flyTo({ center: [event.lng, event.lat], zoom: 15, duration: 1200 });
  }, []);

  /**
   * Open the popup and put `#event=slug` in the URL.
   *
   * Opening from a closed state pushes a history entry, so the phone's Back
   * gesture closes the popup instead of leaving the site; switching between
   * events while one is open replaces that entry, so Back still lands on the
   * pre-popup page. `replace` is for arrival by deep link: there is nothing to
   * go back to, so the current entry is reused and Close simply drops the hash.
   */
  const openEvent = useCallback(
    (event: DanceEvent | null, displayDate?: string, opts: { replace?: boolean } = {}) => {
      setActiveEvent(event);
      setActiveDisplayDate(displayDate ?? null);
      setHighlightedEvent(event);
      const url = event?.slug ? `#event=${event.slug}` : urlWithoutHash();
      const state = window.history.state as PopupHistoryState;
      if (opts.replace) {
        window.history.replaceState(null, '', url);
      } else if (state?.popup) {
        window.history.replaceState({ popup: true }, '', url);
      } else {
        window.history.pushState({ popup: true }, '', url);
      }
    },
    [],
  );

  const closePopup = useCallback(() => {
    setActiveEvent(null);
    setActiveDisplayDate(null);
    const state = window.history.state as PopupHistoryState;
    // The popstate listener below takes the URL back; otherwise (deep link,
    // reload) there is no entry of ours to pop, so just drop the hash.
    if (state?.popup) window.history.back();
    else window.history.replaceState(null, '', urlWithoutHash());
  }, []);

  const eventsById = useMemo(() => {
    const map = new Map<string, DanceEvent>();
    for (const e of events) map.set(e.id, e);
    return map;
  }, [events]);

  const eventsBySlug = useMemo(() => {
    const map = new Map<string, DanceEvent>();
    for (const e of allEventsTyped) if (e.slug) map.set(e.slug, e);
    return map;
  }, [allEventsTyped]);

  // Deep-link handling runs once: resolve the slug from the hash (or the
  // initial prop) on arrival and open that event. Guarded so later filter/date
  // changes — which recreate `ensureEventVisible` — can't re-fire this and
  // reopen a closed popup or undo the user's filters.
  //
  // The popup opens right here, synchronously with hydration. It used to wait
  // for the map to load and fly to the pin, which on a slow connection meant
  // the visitor stared at a blank page through a 1 MB library download, a
  // remote style fetch, the tiles, and a 1.2 s animation before reading a
  // single word. The map is decoration for that visitor; it catches up on its
  // own (see `initialView`).
  const didDeepLinkRef = useRef(false);
  useEffect(() => {
    if (didDeepLinkRef.current) return;
    const slug = slugFromHash(window.location.hash) ?? initialEventSlug;
    if (!slug) return;
    const ev = eventsBySlug.get(slug);
    // Wait for events to load before consuming the one-shot guard.
    if (!ev) return;
    didDeepLinkRef.current = true;
    if (isMappable(ev)) {
      // Ghosts render regardless of filters (and may have no dates), so
      // there's nothing for ensureEventVisible to unhide.
      if (!isGhostEvent(ev)) ensureEventVisible(ev);
      if (mapRef.current) {
        // Map already mounted (hash-link on the homepage with a warm cache):
        // fly rather than re-mount.
        flyToEvent(ev);
      } else {
        setInitialView({ longitude: ev.lng, latitude: ev.lat, zoom: 15 });
      }
      openEvent(ev, undefined, { replace: true });
    }
  }, [eventsBySlug, initialEventSlug, ensureEventVisible, flyToEvent, openEvent]);

  // Back closes the popup (its history entry is popped); Forward reopens it
  // from the hash the entry carries. State is set directly here — the history
  // has already moved, so openEvent/closePopup must not touch it again.
  useEffect(() => {
    const onPopState = (e: PopStateEvent) => {
      const state = e.state as PopupHistoryState;
      const slug = state?.popup ? slugFromHash(window.location.hash) : null;
      const ev = slug ? eventsBySlug.get(slug) : undefined;
      if (ev) {
        setActiveEvent(ev);
        setActiveDisplayDate(null);
        setHighlightedEvent(ev);
      } else {
        setActiveEvent(null);
        setActiveDisplayDate(null);
      }
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [eventsBySlug]);

  const mappableEvents = useMemo(() => events.filter(isMappable), [events]);

  // Search also covers ghosts — archived events and dateless search-only venue
  // records. They open as a translucent dot, never a pin. Archived instances
  // collapse to the most recent per name, and drop out entirely when an active
  // event with the same name (or a search-only venue record for the same
  // series) already holds the search slot.
  const searchableEvents = useMemo(() => {
    const activeNames = new Set(events.map(e => normalizeEventName(e.name)));
    const searchOnly = allEventsTyped.filter(e => e.searchOnly && e.lat != null && e.lng != null);
    const archivedByName = new Map<string, DanceEvent>();
    for (const e of allEventsTyped) {
      if (!e.archived || e.lat == null || e.lng == null) continue;
      const key = normalizeEventName(e.name);
      if (activeNames.has(key)) continue;
      if (searchOnly.some(s => isSeriesInstance(s, e))) continue;
      const prev = archivedByName.get(key);
      if (!prev || e.startDate > prev.startDate) archivedByName.set(key, e);
    }
    return [...mappableEvents, ...searchOnly, ...archivedByName.values()];
  }, [allEventsTyped, events, mappableEvents]);

  const filteredEvents = useMemo(
    () => applyFilters(mappableEvents),
    [mappableEvents, applyFilters],
  );

  const filteredAllEvents = useMemo(() => applyFilters(events), [events, applyFilters]);

  const coordinateOffsets = useMemo(() => staggerCoordinates(filteredEvents), [filteredEvents]);

  const geojson: MarkerCollection = useMemo(() => {
    const features: MarkerFeature[] = filteredEvents.map(event => {
      const offset = coordinateOffsets.get(event.id);
      const lng = event.lng + (offset?.[0] ?? 0);
      const lat = event.lat + (offset?.[1] ?? 0);
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lng, lat] },
        properties: {
          id: event.id,
          __color: primaryColor(event),
          __special: Boolean(event.special),
          __name: event.name,
        },
      };
    });

    return { type: 'FeatureCollection', features };
  }, [filteredEvents, coordinateOffsets]);

  const filteredIds = useMemo(() => new Set(filteredEvents.map(e => e.id)), [filteredEvents]);

  const highlightGeojson = useMemo(() => {
    if (!highlightedEvent || highlightedEvent.lat == null || highlightedEvent.lng == null)
      return null;
    // Don't draw an orphan ring: if the highlighted event has been filtered out
    // (and isn't a ghost, which has no pin to orphan), drop the highlight.
    if (!isGhostEvent(highlightedEvent) && !filteredIds.has(highlightedEvent.id)) return null;
    const offset = coordinateOffsets.get(highlightedEvent.id) ?? [0, 0];
    const lng = highlightedEvent.lng + offset[0];
    const lat = highlightedEvent.lat + offset[1];
    return {
      type: 'FeatureCollection' as const,
      features: [
        {
          type: 'Feature' as const,
          geometry: { type: 'Point' as const, coordinates: [lng, lat] },
          properties: { __color: primaryColor(highlightedEvent) },
        },
      ],
    };
  }, [highlightedEvent, coordinateOffsets, filteredIds]);

  const handleClick = useCallback(
    (e: MapLayerMouseEvent) => {
      const map = mapRef.current?.getMap();
      if (!map) return;

      const feats = map.queryRenderedFeatures(e.point, {
        layers: ['unclustered'],
      });
      // Clicking empty map space deselects: drop the highlight ring and any
      // open popup.
      if (!feats.length) {
        setHighlightedEvent(null);
        closePopup();
        return;
      }

      const props = feats[0].properties;
      if (!props) return;

      const event = eventsById.get(props.id);
      if (event) openEvent(event);
    },
    [eventsById, openEvent, closePopup],
  );

  const handleSearchSelectEvent = useCallback(
    (event: DanceEvent) => {
      if (!isGhostEvent(event)) ensureEventVisible(event);
      if (event.lat != null && event.lng != null) {
        flyToEvent(event);
        openEvent(event);
      }
    },
    [openEvent, flyToEvent, ensureEventVisible],
  );

  const handleFeedSelectEvent = useCallback(
    (event: DanceEvent, displayDate?: string) => {
      openEvent(event, displayDate);
      if (event.lat != null && event.lng != null) {
        // The map unmounts in feed view; fly once it has remounted and loaded.
        pendingFlyToRef.current = event;
        setViewMode('map');
      }
    },
    [openEvent],
  );

  // The map unmounts in feed view, so mark it not-ready; its `onLoad` flips this
  // back to true after it remounts.
  useEffect(() => {
    if (viewMode !== 'map') setMapReady(false);
  }, [viewMode]);

  // Once the map has (re)loaded, run any pending fly-to from a feed selection.
  useEffect(() => {
    if (!mapReady) return;
    const target = pendingFlyToRef.current;
    if (!target) return;
    pendingFlyToRef.current = null;
    flyToEvent(target);
  }, [mapReady, flyToEvent]);

  return (
    <div className="flex flex-col h-full">
      {viewMode === 'map' ? (
        <div className="relative flex-1 overflow-hidden min-h-[40vh]">
          <SearchBar events={searchableEvents} onSelectEvent={handleSearchSelectEvent} />
          {/* Pins that work before the map does. Stacked above the (possibly
              still-empty) canvas until maplibre's own pins are on screen, then
              gone — the overlay's pixels line up with the map's, so the visitor
              sees the same dots throughout. */}
          {!mapReady && (
            <PinOverlay
              geojson={geojson}
              view={initialView}
              highlightedId={highlightedEvent?.id ?? null}
              onSelect={id => {
                const ev = eventsById.get(id);
                if (ev) openEvent(ev);
              }}
              onClear={() => {
                setHighlightedEvent(null);
                closePopup();
              }}
            />
          )}
          <EventMap
            ref={mapRef}
            initialViewState={initialView}
            geojson={geojson}
            highlightGeojson={highlightGeojson}
            highlightColor={highlightedEvent ? primaryColor(highlightedEvent) : '#888'}
            highlightIsGhost={isGhostEvent(highlightedEvent)}
            onClick={handleClick}
            onLoad={() => setMapReady(true)}
          />
        </div>
      ) : (
        <div className="flex-1 overflow-hidden">
          <FeedView
            controls={controls}
            events={filteredAllEvents}
            fromMs={effectiveFromMs}
            toMs={effectiveToMs}
            onSelectEvent={handleFeedSelectEvent}
            onViewModeToggle={() => setViewMode('map')}
          />
        </div>
      )}

      {viewMode === 'map' && (
        <div className="shrink-0">
          <FilterBar
            controls={controls}
            viewMode={viewMode}
            onViewModeToggle={() => setViewMode(v => (v === 'map' ? 'feed' : 'map'))}
          />
        </div>
      )}

      {activeEvent && (
        <EventPopup
          event={activeEvent}
          onClose={closePopup}
          onNavigate={handleSearchSelectEvent}
          displayDate={activeDisplayDate}
          fromMs={effectiveFromMs}
          toMs={effectiveToMs}
        />
      )}
    </div>
  );
}
