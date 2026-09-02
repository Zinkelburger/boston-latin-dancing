'use client';

import { forwardRef } from 'react';
import MapGL, {
  Source,
  Layer,
  type MapRef,
  type LayerProps,
  type ViewState,
} from 'react-map-gl/maplibre';
import type { MapLayerMouseEvent } from 'maplibre-gl';
import type { FeatureCollection, Point } from 'geojson';

// This is the only module that imports maplibre-gl at runtime, so it is the
// only thing that lands in the ~1 MB map chunk. MapView loads it with
// next/dynamic so an event page can hydrate — and open its popup — from the
// small chunks while the map library is still downloading.

const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json';

// Big events (`special`) get a larger pin with a gold ring so festivals stand
// out from the weekly socials at a glance.
const unclusteredLayer: LayerProps = {
  id: 'unclustered',
  type: 'circle',
  filter: ['!', ['has', 'point_count']],
  paint: {
    'circle-color': ['get', '__color'],
    'circle-radius': ['case', ['get', '__special'], 9, 7],
    'circle-stroke-color': ['case', ['get', '__special'], '#facc15', '#ffffff'],
    'circle-stroke-width': ['case', ['get', '__special'], 3, 2],
  },
};

export type MapViewState = Pick<ViewState, 'longitude' | 'latitude' | 'zoom'>;

type Props = {
  initialViewState: MapViewState;
  geojson: FeatureCollection<Point>;
  highlightGeojson: FeatureCollection<Point> | null;
  highlightColor: string;
  /** Ghost (archived / search-only) highlights draw a translucent dot too. */
  highlightIsGhost: boolean;
  onClick: (e: MapLayerMouseEvent) => void;
  onLoad: () => void;
};

const EventMap = forwardRef<MapRef, Props>(function EventMap(
  {
    initialViewState,
    geojson,
    highlightGeojson,
    highlightColor,
    highlightIsGhost,
    onClick,
    onLoad,
  },
  ref,
) {
  return (
    <MapGL
      ref={ref}
      initialViewState={initialViewState}
      mapStyle={MAP_STYLE}
      style={{ width: '100%', height: '100%' }}
      dragRotate={false}
      interactiveLayerIds={['unclustered']}
      onClick={onClick}
      onLoad={onLoad}
    >
      <Source id="events" type="geojson" data={geojson} cluster={false}>
        <Layer {...unclusteredLayer} />
      </Source>
      {highlightGeojson && (
        <Source id="selected-event" type="geojson" data={highlightGeojson}>
          {highlightIsGhost && (
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
              'circle-stroke-color': highlightColor,
              'circle-stroke-width': 3,
              'circle-stroke-opacity': highlightIsGhost ? 0.4 : 0.6,
            }}
          />
        </Source>
      )}
    </MapGL>
  );
});

export default EventMap;
