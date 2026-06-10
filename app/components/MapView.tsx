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
import type { DanceEvent, DanceStyle, DayOfWeek } from '@/types/event';
import { eventMatchesDateRange, dayOfWeekFromIso } from '@/lib/recurrences';
import { STYLE_COLORS } from '@/lib/constants';
import FilterBar from './FilterBar';
import type { DateRangeValue } from './DateRangeSlider';
import EventPopup from './EventPopup';
import SearchBar from './SearchBar';
import FeedView from './FeedView';

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

function dateToDay(d: Date): number {
  return Math.floor(d.getTime() / 86400000);
}

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

export default function MapView() {
  const mapRef = useRef<MapRef>(null);
  const events = useMemo(
    () => (allEvents as DanceEvent[]).filter(e => !e.archived),
    [],
  );

  const WINDOW_DAYS = 45;

  const { sliderMin, sliderMax, defaultFrom, defaultTo } = useMemo(() => {
    const today = dateToDay(new Date());
    return {
      sliderMin: today,
      sliderMax: today + WINDOW_DAYS,
      defaultFrom: today,
      defaultTo: today + 14,
    };
  }, []);

  const [selectedStyles, setSelectedStyles] = useState<DanceStyle[]>([]);
  const [selectedDays, setSelectedDays] = useState<DayOfWeek[]>([]);
  const [dateMode, setDateMode] = useState<'any' | 'custom'>('any');
  const [dateSlider, setDateSlider] = useState<DateRangeValue>({
    fromDay: defaultFrom,
    toDay: defaultTo,
  });
  const [activeEvent, setActiveEvent] = useState<DanceEvent | null>(null);
  const [activeDisplayDate, setActiveDisplayDate] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'map' | 'feed'>('map');
  const [highlightedEvent, setHighlightedEvent] = useState<DanceEvent | null>(null);

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
    for (const e of events) if (e.slug) map.set(e.slug, e);
    return map;
  }, [events]);

  useEffect(() => {
    const hash = window.location.hash;
    const match = hash.match(/^#event=(.+)$/);
    if (!match) return;
    const slug = decodeURIComponent(match[1]);
    const ev = eventsBySlug.get(slug);
    if (ev && ev.lat != null && ev.lng != null) {
      setHighlightedEvent(ev);
      window.history.replaceState(null, '', `#event=${ev.slug}`);
      mapRef.current?.flyTo({ center: [ev.lng, ev.lat], zoom: 15, duration: 1200 });
      const timer = setTimeout(() => openEvent(ev), 1300);
      return () => clearTimeout(timer);
    }
  }, [eventsBySlug, openEvent]);

  const mappableEvents = useMemo(
    () => events.filter(e => e.lat != null && e.lng != null),
    [events],
  );

  const applyFilters = useCallback((source: DanceEvent[]) => {
    const effectiveFrom = dateMode === 'any' ? sliderMin : dateSlider.fromDay;
    const effectiveTo = dateMode === 'any' ? sliderMax : dateSlider.toDay;
    const fromMs = effectiveFrom * 86400000;
    const toMs = (effectiveTo + 1) * 86400000 - 1;

    return source.filter(event => {
      const matchesStyle = selectedStyles.length === 0 ||
        event.styles.some(s => selectedStyles.includes(s));

      const derivedDay = dayOfWeekFromIso(event.startDate);
      const matchesDay = selectedDays.length === 0 ||
        selectedDays.includes(derivedDay) ||
        (event.schedule?.some(s => selectedDays.includes(s.dayOfWeek)) ?? false);

      const matchesDate = eventMatchesDateRange(event, fromMs, toMs);

      return matchesStyle && matchesDay && matchesDate;
    });
  }, [selectedStyles, selectedDays, dateSlider, dateMode, sliderMin, sliderMax]);

  const filteredEvents = useMemo(
    () => applyFilters(mappableEvents),
    [mappableEvents, applyFilters],
  );

  const filteredAllEvents = useMemo(
    () => applyFilters(events),
    [events, applyFilters],
  );

  const { effectiveFromMs, effectiveToMs } = useMemo(() => {
    const effectiveFrom = dateMode === 'any' ? sliderMin : dateSlider.fromDay;
    const effectiveTo = dateMode === 'any' ? sliderMax : dateSlider.toDay;
    return {
      effectiveFromMs: effectiveFrom * 86400000,
      effectiveToMs: (effectiveTo + 1) * 86400000 - 1,
    };
  }, [dateMode, sliderMin, sliderMax, dateSlider]);

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

  const highlightGeojson = useMemo(() => {
    if (!highlightedEvent || highlightedEvent.lat == null || highlightedEvent.lng == null) return null;
    const offset = coordinateOffsets.get(highlightedEvent.id);
    const lng = highlightedEvent.lng + (offset?.[0] ?? 0);
    const lat = highlightedEvent.lat + (offset?.[1] ?? 0);
    return {
      type: 'FeatureCollection' as const,
      features: [{
        type: 'Feature' as const,
        geometry: { type: 'Point' as const, coordinates: [lng, lat] },
        properties: {},
      }],
    };
  }, [highlightedEvent, coordinateOffsets]);

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
      if (event.lat != null && event.lng != null) {
        mapRef.current?.flyTo({ center: [event.lng, event.lat], zoom: 15, duration: 1200 });
        openEvent(event);
      }
    },
    [openEvent],
  );

  const handleFeedSelectEvent = useCallback(
    (event: DanceEvent, displayDate?: string) => {
      openEvent(event, displayDate);
      if (event.lat != null && event.lng != null) {
        setViewMode('map');
        setTimeout(() => {
          mapRef.current?.flyTo({ center: [event.lng!, event.lat!], zoom: 15, duration: 1200 });
        }, 100);
      }
    },
    [openEvent],
  );

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
          >
            <Source id="events" type="geojson" data={geojson} cluster={false}>
              <Layer {...unclusteredLayer} />
            </Source>
            {highlightGeojson && (
              <Source id="selected-event" type="geojson" data={highlightGeojson}>
                <Layer
                  id="selected-ring"
                  type="circle"
                  paint={{
                    'circle-radius': 18,
                    'circle-color': 'transparent',
                    'circle-stroke-color': highlightedEvent ? primaryColor(highlightedEvent) : '#888',
                    'circle-stroke-width': 3,
                    'circle-stroke-opacity': 0.6,
                  }}
                />
              </Source>
            )}
          </MapGL>
        </div>
      ) : (
        <div className="flex-1 overflow-hidden">
          <FeedView
            events={filteredAllEvents}
            selectedDays={selectedDays}
            fromMs={effectiveFromMs}
            toMs={effectiveToMs}
            onSelectEvent={handleFeedSelectEvent}
          />
        </div>
      )}

      <div className="shrink-0">
        <FilterBar
          selectedStyles={selectedStyles}
          onStylesChange={setSelectedStyles}
          selectedDays={selectedDays}
          onDaysChange={setSelectedDays}
          dateMode={dateMode}
          onDateModeChange={setDateMode}
          dateSlider={dateSlider}
          onDateSliderChange={setDateSlider}
          sliderMin={sliderMin}
          sliderMax={sliderMax}
          defaultFrom={defaultFrom}
          defaultTo={defaultTo}
          viewMode={viewMode}
          onViewModeToggle={() => setViewMode(v => v === 'map' ? 'feed' : 'map')}
        />
      </div>

      {activeEvent && (
        <EventPopup
          event={activeEvent}
          onClose={closePopup}
          displayDate={activeDisplayDate}
          fromMs={effectiveFromMs}
          toMs={effectiveToMs}
        />
      )}
    </div>
  );
}
