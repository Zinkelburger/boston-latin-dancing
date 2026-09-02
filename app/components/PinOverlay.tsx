'use client';

import { useLayoutEffect, useRef, useState } from 'react';
import type { FeatureCollection, Point } from 'geojson';
import type { MapViewState } from './EventMap';

/**
 * Clickable event pins that need no map library at all.
 *
 * The real pins are maplibre layers, which means they cannot exist until a
 * ~1 MB chunk has downloaded, the remote style has been fetched, and the first
 * tiles have painted. This overlay draws the same dots at the same pixel
 * positions with plain DOM — the Web Mercator projection below is exactly the
 * one maplibre uses for its initial camera — so a visitor can tap an event the
 * moment React is up, however long the basemap takes. MapView unmounts it when
 * the map fires `load`, and the maplibre pins take over in place.
 *
 * It is deliberately static: no pan or zoom. Anyone who wants to move the map
 * gets the real one as soon as it arrives.
 */

export type PinProps = { id: string; __color: string; __special: boolean; __name: string };

type Props = {
  geojson: FeatureCollection<Point, PinProps>;
  view: MapViewState;
  highlightedId: string | null;
  onSelect: (id: string) => void;
  /** Clicking empty space — same semantics as clicking the bare map. */
  onClear: () => void;
};

const TILE = 512;

function project(
  lng: number,
  lat: number,
  view: MapViewState,
  w: number,
  h: number,
): [number, number] {
  const scale = TILE * Math.pow(2, view.zoom);
  const toX = (l: number) => ((l + 180) / 360) * scale;
  const toY = (la: number) => {
    const r = (la * Math.PI) / 180;
    return ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * scale;
  };
  return [toX(lng) - toX(view.longitude) + w / 2, toY(lat) - toY(view.latitude) + h / 2];
}

export default function PinOverlay({ geojson, view, highlightedId, onSelect, onClear }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      // Inline rather than utility classes: this has to lay out correctly
      // before anything else does, and the project's Tailwind build does not
      // emit `inset-0`.
      style={{ position: 'absolute', inset: 0, zIndex: 5, background: '#f2efe9' }}
      onClick={onClear}
      data-testid="pin-overlay"
    >
      {size.w > 0 &&
        geojson.features.map(f => {
          const [lng, lat] = f.geometry.coordinates;
          const [x, y] = project(lng, lat, view, size.w, size.h);
          if (x < -24 || y < -24 || x > size.w + 24 || y > size.h + 24) return null;
          const { id, __color, __special, __name } = f.properties;
          // Match the maplibre circle layer: radius 7 / stroke 2, or 9 / 3 gold
          // for big events. maplibre draws the stroke outside the radius.
          const diameter = __special ? 24 : 18;
          const border = __special ? '3px solid #facc15' : '2px solid #ffffff';
          const highlighted = id === highlightedId;
          return (
            <button
              key={id}
              type="button"
              aria-label={__name}
              onClick={e => {
                e.stopPropagation();
                onSelect(id);
              }}
              style={{
                position: 'absolute',
                borderRadius: '9999px',
                padding: 0,
                cursor: 'pointer',
                left: x,
                top: y,
                width: diameter,
                height: diameter,
                transform: 'translate(-50%, -50%)',
                background: __color,
                border,
                boxSizing: 'border-box',
                boxShadow: highlighted ? `0 0 0 3px ${__color}99` : undefined,
              }}
            />
          );
        })}
    </div>
  );
}
