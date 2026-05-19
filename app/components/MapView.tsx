'use client';

import { useRef, useState, useMemo, useCallback } from 'react';
import MapGL, {
  Source,
  Layer,
  type MapRef,
  type LayerProps,
} from 'react-map-gl/maplibre';
import type { MapLayerMouseEvent } from 'maplibre-gl';
import type { Feature, FeatureCollection, Point } from 'geojson';

import allEvents from '@/public/events.json';
import allRecurring from '@/public/recurring.json';
import type { DanceEvent, DanceStyle, DayOfWeek, RecurringVenue } from '@/types/event';
import { STYLE_COLORS } from '@/lib/constants';
import FilterBar from './FilterBar';
import type { DateRangeValue } from './DateRangeSlider';
import EventPopup from './EventPopup';
import RecurringPopup from './RecurringPopup';
import SearchBar from './SearchBar';

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

type MarkerProps = { id: string; __color: string; __kind: 'event' | 'recurring' };
type MarkerFeature = Feature<Point, MarkerProps>;
type MarkerCollection = FeatureCollection<Point, MarkerProps>;

const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json';

function dateToDay(d: Date): number {
  return Math.floor(d.getTime() / 86400000);
}

function primaryColor(event: { styles: DanceStyle[] }): string {
  if (event.styles.includes('bachata')) return STYLE_COLORS.bachata;
  if (event.styles.includes('salsa')) return STYLE_COLORS.salsa;
  if (event.styles.includes('kizomba')) return STYLE_COLORS.kizomba;
  if (event.styles.includes('zouk')) return STYLE_COLORS.zouk;
  if (event.styles.includes('merengue')) return STYLE_COLORS.merengue;
  return STYLE_COLORS.other;
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
  const events = allEvents as DanceEvent[];
  const recurringVenues = allRecurring as RecurringVenue[];

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
  const [activeRecurring, setActiveRecurring] = useState<RecurringVenue | null>(null);

  const eventsById = useMemo(() => {
    const map = new Map<string, DanceEvent>();
    for (const e of events) map.set(e.id, e);
    return map;
  }, [events]);

  const recurringById = useMemo(() => {
    const map = new Map<string, RecurringVenue>();
    for (const r of recurringVenues) map.set(r.id, r);
    return map;
  }, [recurringVenues]);

  const mappableEvents = useMemo(
    () => events.filter(e => e.lat != null && e.lng != null),
    [events],
  );

  const filteredEvents = useMemo(() => {
    const effectiveFrom = dateMode === 'any' ? sliderMin : dateSlider.fromDay;
    const effectiveTo = dateMode === 'any' ? sliderMax : dateSlider.toDay;
    const fromMs = effectiveFrom * 86400000;
    const toMs = (effectiveTo + 1) * 86400000 - 1;

    return mappableEvents.filter(event => {
      const matchesStyle = selectedStyles.length === 0 ||
        event.styles.some(s => selectedStyles.includes(s));

      const matchesDay = selectedDays.length === 0 ||
        selectedDays.includes(event.dayOfWeek);

      const eventMs = new Date(event.startDate).getTime();
      const matchesDate = eventMs >= fromMs && eventMs <= toMs;

      return matchesStyle && matchesDay && matchesDate;
    });
  }, [mappableEvents, selectedStyles, selectedDays, dateSlider, dateMode, sliderMin, sliderMax]);

  const filteredRecurring = useMemo(() => {
    return recurringVenues.filter(venue => {
      const matchesStyle = selectedStyles.length === 0 ||
        venue.styles.some(s => selectedStyles.includes(s));

      const matchesDay = selectedDays.length === 0 ||
        venue.schedule.some(s => selectedDays.includes(s.dayOfWeek));

      return matchesStyle && matchesDay;
    });
  }, [recurringVenues, selectedStyles, selectedDays]);

  const allMapItems = useMemo(() => {
    const eventItems = filteredEvents.map(e => ({
      id: e.id, lat: e.lat!, lng: e.lng!,
    }));
    const recurringItems = filteredRecurring.map(r => ({
      id: r.id, lat: r.lat, lng: r.lng,
    }));
    return [...eventItems, ...recurringItems];
  }, [filteredEvents, filteredRecurring]);

  const coordinateOffsets = useMemo(
    () => staggerCoordinates(allMapItems),
    [allMapItems],
  );

  const geojson: MarkerCollection = useMemo(() => {
    const eventFeatures: MarkerFeature[] = filteredEvents.map(event => {
      const offset = coordinateOffsets.get(event.id);
      const lng = event.lng! + (offset?.[0] ?? 0);
      const lat = event.lat! + (offset?.[1] ?? 0);
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lng, lat] },
        properties: { id: event.id, __color: primaryColor(event), __kind: 'event' },
      };
    });

    const recurringFeatures: MarkerFeature[] = filteredRecurring.map(venue => {
      const offset = coordinateOffsets.get(venue.id);
      const lng = venue.lng + (offset?.[0] ?? 0);
      const lat = venue.lat + (offset?.[1] ?? 0);
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lng, lat] },
        properties: { id: venue.id, __color: primaryColor(venue), __kind: 'recurring' },
      };
    });

    return { type: 'FeatureCollection', features: [...eventFeatures, ...recurringFeatures] };
  }, [filteredEvents, filteredRecurring, coordinateOffsets]);

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

      if (props.__kind === 'recurring') {
        const venue = recurringById.get(props.id);
        if (venue) {
          setActiveRecurring(venue);
          setActiveEvent(null);
        }
      } else {
        const event = eventsById.get(props.id);
        if (event) {
          setActiveEvent(event);
          setActiveRecurring(null);
        }
      }
    },
    [eventsById, recurringById],
  );

  const handleSearchSelectEvent = useCallback(
    (event: DanceEvent) => {
      if (event.lat != null && event.lng != null) {
        mapRef.current?.flyTo({ center: [event.lng, event.lat], zoom: 14, duration: 1200 });
        setActiveEvent(event);
        setActiveRecurring(null);
      }
    },
    [],
  );

  const totalVisible = filteredEvents.length + filteredRecurring.length;
  const totalAll = mappableEvents.length + recurringVenues.length;

  return (
    <div className="flex flex-col h-full">
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
        </MapGL>
      </div>

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
          totalCount={totalAll}
          visibleCount={totalVisible}
        />
      </div>

      {activeEvent && (
        <EventPopup
          event={activeEvent}
          onClose={() => setActiveEvent(null)}
        />
      )}

      {activeRecurring && (
        <RecurringPopup
          venue={activeRecurring}
          onClose={() => setActiveRecurring(null)}
        />
      )}
    </div>
  );
}
