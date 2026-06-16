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

export type DatePreset = 'today' | 'tomorrow' | 'weekend' | 'next3' | 'next7' | 'all';

const PRESET_LABELS: Record<DatePreset, string> = {
  today: 'Today',
  tomorrow: 'Tomorrow',
  weekend: 'This Weekend',
  next3: 'Next 3 Days',
  next7: 'Next 7 Days',
  all: 'All',
};

function computePresetRange(preset: DatePreset, today: number): { fromDay: number; toDay: number } | null {
  switch (preset) {
    case 'today':
      return { fromDay: today, toDay: today };
    case 'tomorrow':
      return { fromDay: today + 1, toDay: today + 1 };
    case 'weekend': {
      const todayDate = new Date(today * 86400000);
      const dow = todayDate.getUTCDay();
      // 0=Sun, 6=Sat
      if (dow === 0) return { fromDay: today, toDay: today }; // Sunday: just today
      if (dow === 6) return { fromDay: today, toDay: today + 1 }; // Saturday: Sat+Sun
      const daysUntilSat = 6 - dow;
      return { fromDay: today + daysUntilSat, toDay: today + daysUntilSat + 1 };
    }
    case 'next3':
      return { fromDay: today, toDay: today + 2 };
    case 'next7':
      return { fromDay: today, toDay: today + 6 };
    case 'all':
      return null;
  }
}

export { PRESET_LABELS };

export default function MapView({ initialEventSlug }: { initialEventSlug?: string } = {}) {
  const mapRef = useRef<MapRef>(null);
  const allEventsTyped = useMemo(() => allEvents as DanceEvent[], []);
  const events = useMemo(
    () => allEventsTyped.filter(e => !e.archived),
    [allEventsTyped],
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
  const [datePreset, setDatePreset] = useState<DatePreset | null>(null);
  const [activeEvent, setActiveEvent] = useState<DanceEvent | null>(null);
  const [activeDisplayDate, setActiveDisplayDate] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'map' | 'feed'>('map');
  const [highlightedEvent, setHighlightedEvent] = useState<DanceEvent | null>(null);

  const handlePresetChange = useCallback((preset: DatePreset | null) => {
    setDatePreset(preset);
    if (!preset) {
      setDateMode('any');
      return;
    }
    const today = dateToDay(new Date());
    const range = computePresetRange(preset, today);
    if (range) {
      setDateMode('custom');
      setDateSlider(range);
    } else {
      setDateMode('any');
    }
  }, []);

  const handleDateModeChange = useCallback((mode: 'any' | 'custom') => {
    setDateMode(mode);
    setDatePreset(null);
  }, []);

  const handleDateSliderChange = useCallback((v: DateRangeValue) => {
    setDateSlider(v);
    setDatePreset(null);
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

  useEffect(() => {
    const hash = window.location.hash;
    const match = hash.match(/^#event=(.+)$/);
    const slug = match ? decodeURIComponent(match[1]) : initialEventSlug;
    if (!slug) return;
    const ev = eventsBySlug.get(slug);
    if (ev && ev.lat != null && ev.lng != null) {
      setHighlightedEvent(ev);
      window.history.replaceState(null, '', `#event=${ev.slug}`);
      mapRef.current?.flyTo({ center: [ev.lng, ev.lat], zoom: 15, duration: 1200 });
      const timer = setTimeout(() => openEvent(ev), 1300);
      return () => clearTimeout(timer);
    }
  }, [eventsBySlug, openEvent, initialEventSlug]);

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
            events={filteredAllEvents}
            selectedDays={selectedDays}
            fromMs={effectiveFromMs}
            toMs={effectiveToMs}
            onSelectEvent={handleFeedSelectEvent}
            datePreset={datePreset}
            onPresetChange={handlePresetChange}
            selectedStyles={selectedStyles}
            onStylesChange={setSelectedStyles}
            onDaysChange={setSelectedDays}
            onViewModeToggle={() => setViewMode('map')}
            dateMode={dateMode}
            onDateModeChange={handleDateModeChange}
            dateSlider={dateSlider}
            onDateSliderChange={handleDateSliderChange}
            sliderMin={sliderMin}
            sliderMax={sliderMax}
            defaultFrom={defaultFrom}
            defaultTo={defaultTo}
          />
        </div>
      )}

      {viewMode === 'map' && (
        <div className="shrink-0">
          <FilterBar
            selectedStyles={selectedStyles}
            onStylesChange={setSelectedStyles}
            selectedDays={selectedDays}
            onDaysChange={setSelectedDays}
            dateMode={dateMode}
            onDateModeChange={handleDateModeChange}
            dateSlider={dateSlider}
            onDateSliderChange={handleDateSliderChange}
            sliderMin={sliderMin}
            sliderMax={sliderMax}
            defaultFrom={defaultFrom}
            defaultTo={defaultTo}
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
