'use client';

import { useRef, useCallback, useEffect, useState } from 'react';
import { formatShort as formatLabel } from '@/lib/dates';

export type DateRangeValue = { fromDay: number; toDay: number };

type Props = {
  minDay: number;
  maxDay: number;
  value: DateRangeValue;
  onChange: (v: DateRangeValue) => void;
};

const TRACK_H = 6;
const THUMB_R = 9;

type Thumb = 'from' | 'to';

export default function DateRangeSlider({ minDay, maxDay, value, onChange }: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const dragging = useRef<Thumb | null>(null);
  const [localValue, setLocalValue] = useState(value);

  useEffect(() => {
    setLocalValue(value);
  }, [value]);

  const range = maxDay - minDay || 1;

  const pctFrom = ((localValue.fromDay - minDay) / range) * 100;
  const pctTo = ((localValue.toDay - minDay) / range) * 100;

  const dayFromPx = useCallback(
    (clientX: number): number => {
      const track = trackRef.current;
      if (!track) return minDay;
      const rect = track.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      return Math.round(minDay + pct * range);
    },
    [minDay, range],
  );

  const handleTrackClick = useCallback(
    (e: React.MouseEvent) => {
      if (dragging.current) return;
      const day = Math.max(minDay, Math.min(maxDay, dayFromPx(e.clientX)));
      const distFrom = Math.abs(day - localValue.fromDay);
      const distTo = Math.abs(day - localValue.toDay);
      const nearest = distFrom <= distTo ? 'from' : 'to';
      const next =
        nearest === 'from'
          ? { fromDay: Math.min(day, localValue.toDay), toDay: localValue.toDay }
          : { fromDay: localValue.fromDay, toDay: Math.max(day, localValue.fromDay) };
      setLocalValue(next);
      onChange(next);
    },
    [dayFromPx, minDay, maxDay, localValue, onChange],
  );

  const handlePointerDown = useCallback(
    (thumb: Thumb) => (e: React.PointerEvent) => {
      e.preventDefault();
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
      dragging.current = thumb;
    },
    [],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!dragging.current) return;
      const day = Math.max(minDay, Math.min(maxDay, dayFromPx(e.clientX)));
      setLocalValue(prev => {
        if (dragging.current === 'from') {
          if (day > prev.toDay) {
            dragging.current = 'to';
            return { fromDay: prev.toDay, toDay: day };
          }
          return { ...prev, fromDay: day };
        } else {
          if (day < prev.fromDay) {
            dragging.current = 'from';
            return { fromDay: day, toDay: prev.fromDay };
          }
          return { ...prev, toDay: day };
        }
      });
    },
    [dayFromPx, minDay, maxDay],
  );

  const handlePointerUp = useCallback(() => {
    if (dragging.current) {
      dragging.current = null;
      onChange(localValue);
    }
  }, [localValue, onChange]);

  useEffect(() => {
    const up = () => {
      if (dragging.current) {
        dragging.current = null;
        onChange(localValue);
      }
    };
    window.addEventListener('pointerup', up);
    return () => window.removeEventListener('pointerup', up);
  }, [localValue, onChange]);

  // Keyboard: arrows step a day (a week with Shift); Home/End jump to the
  // thumb's limit. A thumb can never cross the other one.
  const handleKeyDown = useCallback(
    (thumb: Thumb) => (e: React.KeyboardEvent) => {
      const step = e.shiftKey ? 7 : 1;
      const current = thumb === 'from' ? localValue.fromDay : localValue.toDay;
      const low = thumb === 'from' ? minDay : localValue.fromDay;
      const high = thumb === 'from' ? localValue.toDay : maxDay;
      let target: number;
      switch (e.key) {
        case 'ArrowLeft':
        case 'ArrowDown':
          target = current - step;
          break;
        case 'ArrowRight':
        case 'ArrowUp':
          target = current + step;
          break;
        case 'Home':
          target = low;
          break;
        case 'End':
          target = high;
          break;
        default:
          return;
      }
      e.preventDefault();
      const day = Math.max(low, Math.min(high, target));
      if (day === current) return;
      const next =
        thumb === 'from'
          ? { fromDay: day, toDay: localValue.toDay }
          : { fromDay: localValue.fromDay, toDay: day };
      setLocalValue(next);
      onChange(next);
    },
    [localValue, minDay, maxDay, onChange],
  );

  const fromLabel = formatLabel(localValue.fromDay);
  const toLabel = formatLabel(localValue.toDay);
  const daySpan = localValue.toDay - localValue.fromDay;

  const thumbStyle = (pct: number): React.CSSProperties => ({
    left: `${pct}%`,
    top: 2,
    width: THUMB_R * 2,
    height: THUMB_R * 2,
    marginLeft: -THUMB_R,
    borderRadius: '50%',
    background: '#ffffff',
    border: '2.5px solid #e11d48',
    boxShadow: '0 1px 4px rgba(0,0,0,0.18)',
    cursor: 'grab',
    zIndex: 2,
    touchAction: 'none',
  });

  const thumbClass =
    'absolute focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-400 focus-visible:ring-offset-2';

  return (
    <div className="flex flex-col gap-1" style={{ minWidth: 180 }}>
      {/* Labels row */}
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold text-rose-600">{fromLabel}</span>
        <span className="text-gray-400 px-1">{daySpan === 0 ? '1 day' : `${daySpan} days`}</span>
        <span className="font-semibold text-rose-600">{toLabel}</span>
      </div>

      {/* Track */}
      <div
        ref={trackRef}
        className="relative select-none touch-none"
        style={{ height: THUMB_R * 2 + 4, cursor: 'pointer' }}
        onClick={handleTrackClick}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      >
        {/* Background track */}
        <div
          className="absolute rounded-full bg-gray-200 pointer-events-none"
          style={{
            left: 0,
            right: 0,
            top: THUMB_R - TRACK_H / 2 + 2,
            height: TRACK_H,
          }}
        />

        {/* Active range fill */}
        <div
          className="absolute rounded-full pointer-events-none"
          style={{
            left: `${pctFrom}%`,
            width: `${Math.max(0, pctTo - pctFrom)}%`,
            top: THUMB_R - TRACK_H / 2 + 2,
            height: TRACK_H,
            background: 'linear-gradient(90deg, #e11d48, #f43f5e)',
          }}
        />

        {/* From thumb */}
        <div
          role="slider"
          tabIndex={0}
          aria-label="Start date"
          aria-valuemin={minDay}
          aria-valuemax={localValue.toDay}
          aria-valuenow={localValue.fromDay}
          aria-valuetext={fromLabel}
          onPointerDown={handlePointerDown('from')}
          onKeyDown={handleKeyDown('from')}
          className={thumbClass}
          style={thumbStyle(pctFrom)}
        />

        {/* To thumb */}
        <div
          role="slider"
          tabIndex={0}
          aria-label="End date"
          aria-valuemin={localValue.fromDay}
          aria-valuemax={maxDay}
          aria-valuenow={localValue.toDay}
          aria-valuetext={toLabel}
          onPointerDown={handlePointerDown('to')}
          onKeyDown={handleKeyDown('to')}
          className={thumbClass}
          style={thumbStyle(pctTo)}
        />
      </div>

      {/* Subtle min/max labels */}
      <div className="flex items-center justify-between text-[10px] text-gray-300 -mt-0.5">
        <span>{formatLabel(minDay)}</span>
        <span>{formatLabel(maxDay)}</span>
      </div>
    </div>
  );
}
