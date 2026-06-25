'use client';

import { useRef, useState, useMemo, useCallback, useEffect } from 'react';
import MapGL, {
  Source,
  Layer,
  type MapRef,
  type LayerProps,
} from 'react-map-gl/maplibre';
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

const unclusteredLayer: LayerProps = {
  id: 'unclustered',
  type: 'circle',
  filter: ['!', ['has', 'point_count']],
  paint: {
    'circle-color': ['get', '__color'],
    'circle-radius': 7,
    'circle-stroke-color': '#ffffff',
    'circle-stroke-width': 2,
  },
};

type MarkerProps = { id: string; __color: string };
type MarkerFeature = Feature<Point, MarkerProps>;
type MarkerCollection = FeatureCollection<Point, MarkerProps>;

const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json';

function primaryColor(event: { styles: DanceStyle[] }): string {
  const primary = event.styles[0];
  return (primary && STYLE_COLORS[primary]) || STYLE_COLORS.other;
}

function staggerCoordinates(items: { id: string; lat: number; lng: number }[]): Map<string, [number, number]> {
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
    () => allEventsTyped.filter(e => !e.archived),
    [allEventsTyped],
  );

  const { controls, applyFilters, effectiveFromMs, effectiveToMs, ensureEventVisible } = useEventFilters();

  const [activeEvent, setActiveEvent] = useState<DanceEvent | null>(null);
  const [activeDisplayDate, setActiveDisplayDate] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'map' | 'feed'>('map');
  const [highlightedEvent, setHighlightedEvent] = useState<DanceEvent | null>(null);
  /** True once the (re)mounted map has fired its `load` event and is safe to drive. */
  const [mapReady, setMapReady] = useState(false);

  /** Event to fly to once the map is ready (set by deep-links and feed selection). */
  const pendingFlyToRef = useRef<DanceEvent | null>(null);
  /** When true, open the event popup after the pending fly-to settles (deep-link UX). */
  const pendingPopupRef = useRef(false);

  const flyToEvent = useCallback((event: DanceEvent) => {
    if (event.lat == null || event.lng == null) return;
    mapRef.current?.flyTo({ center: [event.lng, event.lat], zoom: 15, duration: 1200 });
  }, []);

  const openEvent = useCallback((event: DanceEvent | null, displayDate?: string) => {
    setActiveEvent(event);
    setActiveDisplayDate(displayDate ?? null);
    setHighlightedEvent(event);
    window.history.replaceState(null, '', event?.slug ? `#event=${event.slug}` : ' ');
  }, []);

  const closePopup = useCallback(() => {
    setActiveEvent(null);
    setActiveDisplayDate(null);
    window.history.replaceState(null, '', ' ');
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
  const didDeepLinkRef = useRef(false);
  useEffect(() => {
    if (didDeepLinkRef.current) return;
    const hash = window.location.hash;
    const match = hash.match(/^#event=(.+)$/);
    const slug = match ? decodeURIComponent(match[1]) : initialEventSlug;
    if (!slug) return;
    const ev = eventsBySlug.get(slug);
    // Wait for events to load before consuming the one-shot guard.
    if (!ev) return;
    didDeepLinkRef.current = true;
    if (ev.lat != null && ev.lng != null) {
      ensureEventVisible(ev);
      setHighlightedEvent(ev);
      window.history.replaceState(null, '', `#event=${ev.slug}`);
      pendingFlyToRef.current = ev;
      pendingPopupRef.current = true;
    }
  }, [eventsBySlug, initialEventSlug, ensureEventVisible]);

  const mappableEvents = useMemo(
    () => events.filter(e => e.lat != null && e.lng != null),
    [events],
  );

  const filteredEvents = useMemo(
    () => applyFilters(mappableEvents),
    [mappableEvents, applyFilters],
  );

  const filteredAllEvents = useMemo(
    () => applyFilters(events),
    [events, applyFilters],
  );

  const allMapItems = useMemo(() => {
    return filteredEvents.map(e => ({
      id: e.id, lat: e.lat!, lng: e.lng!,
    }));
  }, [filteredEvents]);

  const coordinateOffsets = useMemo(
    () => staggerCoordinates(allMapItems),
    [allMapItems],
  );

  const geojson: MarkerCollection = useMemo(() => {
    const features: MarkerFeature[] = filteredEvents.map(event => {
      const offset = coordinateOffsets.get(event.id);
      const lng = event.lng! + (offset?.[0] ?? 0);
      const lat = event.lat! + (offset?.[1] ?? 0);
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lng, lat] },
        properties: { id: event.id, __color: primaryColor(event) },
      };
    });

    return { type: 'FeatureCollection', features };
  }, [filteredEvents, coordinateOffsets]);

  const filteredIds = useMemo(
    () => new Set(filteredEvents.map(e => e.id)),
    [filteredEvents],
  );

  const highlightGeojson = useMemo(() => {
    if (!highlightedEvent || highlightedEvent.lat == null || highlightedEvent.lng == null) return null;
    // Don't draw an orphan ring: if the highlighted event has been filtered out
    // (and isn't an archived deep-link), drop the highlight with its pin.
    if (!highlightedEvent.archived && !filteredIds.has(highlightedEvent.id)) return null;
    const offset = coordinateOffsets.get(highlightedEvent.id) ?? [0, 0];
    const lng = highlightedEvent.lng + offset[0];
    const lat = highlightedEvent.lat + offset[1];
    return {
      type: 'FeatureCollection' as const,
      features: [{
        type: 'Feature' as const,
        geometry: { type: 'Point' as const, coordinates: [lng, lat] },
        properties: { __color: primaryColor(highlightedEvent) },
      }],
    };
  }, [highlightedEvent, coordinateOffsets, filteredIds]);

  const handleClick = useCallback(
    (e: MapLayerMouseEvent) => {
      const map = mapRef.current?.getMap();
      if (!map) return;

      const feats = map.queryRenderedFeatures(e.point, {
        layers: ['unclustered'],
      });
      if (!feats.length) return;

      const props = feats[0].properties;
      if (!props) return;

      const event = eventsById.get(props.id);
      if (event) openEvent(event);
    },
    [eventsById, openEvent],
  );

  const handleSearchSelectEvent = useCallback(
    (event: DanceEvent) => {
      ensureEventVisible(event);
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

  // Once the map has loaded, run any pending fly-to (from a deep-link or feed
  // selection) and, for deep-links, open the popup after the camera settles.
  useEffect(() => {
    if (!mapReady) return;
    const target = pendingFlyToRef.current;
    if (!target) return;
    pendingFlyToRef.current = null;
    flyToEvent(target);
    if (pendingPopupRef.current) {
      pendingPopupRef.current = false;
      const map = mapRef.current?.getMap();
      // Fallback: open the popup after a timeout in case flyTo doesn't trigger
      // moveend (camera already at target, animation cancelled, etc.).
      const fallback = setTimeout(() => openEvent(target), 1500);
      if (map) {
        map.once('moveend', () => {
          clearTimeout(fallback);
          openEvent(target);
        });
      }
    }
  }, [mapReady, flyToEvent, openEvent]);

  return (
    <div className="flex flex-col h-full">
      {viewMode === 'map' ? (
        <div className="relative flex-1 overflow-hidden min-h-[40vh]">
          <SearchBar
            events={mappableEvents}
            onSelectEvent={handleSearchSelectEvent}
          />
          <MapGL
            ref={mapRef}
            initialViewState={{ longitude: -71.08, latitude: 42.36, zoom: 11 }}
            mapStyle={MAP_STYLE}
            style={{ width: '100%', height: '100%' }}
            dragRotate={false}
            interactiveLayerIds={['unclustered']}
            onClick={handleClick}
            onLoad={() => setMapReady(true)}
          >
            <Source id="events" type="geojson" data={geojson} cluster={false}>
              <Layer {...unclusteredLayer} />
            </Source>
            {highlightGeojson && (
              <Source id="selected-event" type="geojson" data={highlightGeojson}>
                {highlightedEvent?.archived && (
                  <Layer
                    id="selected-dot"
                    type="circle"
                    paint={{
                      'circle-color': ['get', '__color'],
                      'circle-radius': 7,
                      'circle-stroke-color': '#ffffff',
                      'circle-stroke-width': 2,
                      'circle-opacity': 0.5,
                    }}
                  />
                )}
                <Layer
                  id="selected-ring"
                  type="circle"
                  paint={{
                    'circle-radius': 18,
                    'circle-color': 'transparent',
                    'circle-stroke-color': highlightedEvent ? primaryColor(highlightedEvent) : '#888',
                    'circle-stroke-width': 3,
                    'circle-stroke-opacity': highlightedEvent?.archived ? 0.4 : 0.6,
                  }}
                />
              </Source>
            )}
          </MapGL>
        </div>
      ) : (
        <div className="flex-1 overflow-hidden">
          <FeedView
            {...controls}
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
            {...controls}
            viewMode={viewMode}
            onViewModeToggle={() => setViewMode(v => v === 'map' ? 'feed' : 'map')}
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
