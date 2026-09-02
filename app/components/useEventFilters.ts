'use client';

import { useState, useMemo, useCallback, useEffect } from 'react';
import type { DanceEvent, DanceStyle, DayOfWeek } from '@/types/event';
import { eventMatchesDateRange, matchesDay } from '@/lib/recurrences';
import { dateToDay, dayStartMs } from '@/lib/dates';
import { computePresetRange, type DatePreset } from '@/lib/date-presets';
import type { DateRangeValue } from './DateRangeSlider';

/**
 * For an event that falls outside the current date window, return the
 * epoch-day we need to include in the range so the event becomes visible on
 * the map.
 */
function eventTargetDay(event: DanceEvent): number {
  if (event.recurrences?.length) {
    const now = Date.now();
    const nearest = event.recurrences
      .map(iso => new Date(iso).getTime())
      .filter(ms => ms >= now)
      .sort((a, b) => a - b)[0];
    return dateToDay(new Date(nearest ?? new Date(event.startDate).getTime()));
  }
  return dateToDay(new Date(event.startDate));
}

/** How many days ahead the date slider can reach. */
const WINDOW_DAYS = 45;

/** How often to check whether the calendar day has rolled over. */
const DAY_CHECK_MS = 60_000;

/** The bundle of state + handlers consumed by FilterBar and FeedView. */
export type FilterControlsProps = {
  selectedStyles: DanceStyle[];
  onStylesChange: (styles: DanceStyle[]) => void;
  selectedDays: DayOfWeek[];
  onDaysChange: (days: DayOfWeek[]) => void;
  specialOnly: boolean;
  onSpecialOnlyChange: (v: boolean) => void;
  dateMode: 'any' | 'custom';
  onDateModeChange: (mode: 'any' | 'custom') => void;
  dateSlider: DateRangeValue;
  onDateSliderChange: (v: DateRangeValue) => void;
  sliderMin: number;
  sliderMax: number;
  defaultFrom: number;
  defaultTo: number;
  datePreset: DatePreset | null;
  onPresetChange: (preset: DatePreset | null) => void;
};

/**
 * Today's Boston epoch-day, re-checked every minute so a tab left open past
 * midnight moves its window forward instead of keeping yesterday as "today".
 * State only changes when the day does, so nothing re-renders in between.
 */
function useTodayDay(): number {
  const [today, setToday] = useState(() => dateToDay(new Date()));
  useEffect(() => {
    const id = setInterval(() => {
      const day = dateToDay(new Date());
      setToday(prev => (prev === day ? prev : day));
    }, DAY_CHECK_MS);
    return () => clearInterval(id);
  }, []);
  return today;
}

/**
 * Owns all event-filter state (style / day / date) and derives the active date
 * window and the `applyFilters` predicate. Returns a single `controls` bundle to
 * hand to the filter UIs, so MapView doesn't drill ~15 separate props.
 */
export function useEventFilters() {
  const today = useTodayDay();
  const { sliderMin, sliderMax, defaultFrom, defaultTo } = useMemo(() => ({
    sliderMin: today,
    sliderMax: today + WINDOW_DAYS,
    defaultFrom: today,
    defaultTo: today + 14,
  }), [today]);

  const [selectedStyles, setSelectedStyles] = useState<DanceStyle[]>([]);
  const [selectedDays, setSelectedDays] = useState<DayOfWeek[]>([]);
  const [specialOnly, setSpecialOnly] = useState(false);
  const [dateMode, setDateMode] = useState<'any' | 'custom'>('any');
  const [dateSlider, setDateSlider] = useState<DateRangeValue>({
    fromDay: defaultFrom,
    toDay: defaultTo,
  });
  const [datePreset, setDatePreset] = useState<DatePreset | null>(null);

  const handlePresetChange = useCallback((preset: DatePreset | null) => {
    setDatePreset(preset);
    if (!preset) {
      setDateMode('any');
      return;
    }
    const range = computePresetRange(preset);
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

  const { effectiveFromMs, effectiveToMs } = useMemo(() => {
    const effectiveFrom = dateMode === 'any' ? sliderMin : dateSlider.fromDay;
    const effectiveTo = dateMode === 'any' ? sliderMax : dateSlider.toDay;
    return {
      effectiveFromMs: dayStartMs(effectiveFrom),
      effectiveToMs: dayStartMs(effectiveTo + 1) - 1,
    };
  }, [dateMode, sliderMin, sliderMax, dateSlider]);

  const applyFilters = useCallback(<T extends DanceEvent>(source: T[]): T[] => {
    const range = { fromMs: effectiveFromMs, toMs: effectiveToMs };
    return source.filter(event => {
      const matchesStyle = selectedStyles.length === 0 ||
        event.styles.some(s => selectedStyles.includes(s));

      const matchesSpecial = !specialOnly || Boolean(event.special);

      return matchesStyle
        && matchesSpecial
        && eventMatchesDateRange(event, effectiveFromMs, effectiveToMs)
        && matchesDay(event, selectedDays, range);
    });
  }, [selectedStyles, selectedDays, specialOnly, effectiveFromMs, effectiveToMs]);

  /** Clear any filters that would hide `event`, so it appears on the map. */
  const ensureEventVisible = useCallback((event: DanceEvent) => {
    const matchesStyle = selectedStyles.length === 0 ||
      event.styles.some(s => selectedStyles.includes(s));
    if (!matchesStyle) setSelectedStyles([]);

    // The day filter is judged over the whole slider window, not just the
    // current range: the range may widen below to reach the event.
    const window = { fromMs: dayStartMs(sliderMin), toMs: dayStartMs(sliderMax + 1) - 1 };
    if (!matchesDay(event, selectedDays, window)) setSelectedDays([]);

    if (specialOnly && !event.special) setSpecialOnly(false);

    if (eventMatchesDateRange(event, effectiveFromMs, effectiveToMs)) return;

    const targetDay = eventTargetDay(event);
    const currentFrom = dateMode === 'any' ? sliderMin : dateSlider.fromDay;
    const currentTo = dateMode === 'any' ? sliderMax : dateSlider.toDay;

    setDateMode('custom');
    setDateSlider({
      fromDay: Math.max(sliderMin, Math.min(currentFrom, targetDay)),
      toDay: Math.min(sliderMax, Math.max(currentTo, targetDay)),
    });
    setDatePreset(null);
  }, [effectiveFromMs, effectiveToMs, dateMode, sliderMin, sliderMax, dateSlider, selectedStyles, selectedDays, specialOnly]);

  const controls: FilterControlsProps = {
    selectedStyles, onStylesChange: setSelectedStyles,
    selectedDays, onDaysChange: setSelectedDays,
    specialOnly, onSpecialOnlyChange: setSpecialOnly,
    dateMode, onDateModeChange: handleDateModeChange,
    dateSlider, onDateSliderChange: handleDateSliderChange,
    sliderMin, sliderMax, defaultFrom, defaultTo,
    datePreset, onPresetChange: handlePresetChange,
  };

  return { controls, applyFilters, effectiveFromMs, effectiveToMs, ensureEventVisible };
}
