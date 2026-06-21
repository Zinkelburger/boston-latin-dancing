'use client';

import { useState, useMemo, useCallback } from 'react';
import type { DanceEvent, DanceStyle, DayOfWeek } from '@/types/event';
import { eventMatchesDateRange, dayOfWeekFromIso } from '@/lib/recurrences';
import { dateToDay } from '@/lib/dates';
import { computePresetRange, type DatePreset } from '@/lib/date-presets';
import type { DateRangeValue } from './DateRangeSlider';

/** How many days ahead the date slider can reach. */
const WINDOW_DAYS = 45;

/** The bundle of state + handlers consumed by FilterBar and FeedView. */
export type FilterControlsProps = {
  selectedStyles: DanceStyle[];
  onStylesChange: (styles: DanceStyle[]) => void;
  selectedDays: DayOfWeek[];
  onDaysChange: (days: DayOfWeek[]) => void;
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
 * Owns all event-filter state (style / day / date) and derives the active date
 * window and the `applyFilters` predicate. Returns a single `controls` bundle to
 * hand to the filter UIs, so MapView doesn't drill ~15 separate props.
 */
export function useEventFilters() {
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
      effectiveFromMs: effectiveFrom * 86400000,
      effectiveToMs: (effectiveTo + 1) * 86400000 - 1,
    };
  }, [dateMode, sliderMin, sliderMax, dateSlider]);

  const applyFilters = useCallback((source: DanceEvent[]) => {
    return source.filter(event => {
      const matchesStyle = selectedStyles.length === 0 ||
        event.styles.some(s => selectedStyles.includes(s));

      const derivedDay = dayOfWeekFromIso(event.startDate);
      const matchesDay = selectedDays.length === 0 ||
        selectedDays.includes(derivedDay) ||
        (event.schedule?.some(s => selectedDays.includes(s.dayOfWeek)) ?? false);

      const matchesDate = eventMatchesDateRange(event, effectiveFromMs, effectiveToMs);

      return matchesStyle && matchesDay && matchesDate;
    });
  }, [selectedStyles, selectedDays, effectiveFromMs, effectiveToMs]);

  const controls: FilterControlsProps = {
    selectedStyles, onStylesChange: setSelectedStyles,
    selectedDays, onDaysChange: setSelectedDays,
    dateMode, onDateModeChange: handleDateModeChange,
    dateSlider, onDateSliderChange: handleDateSliderChange,
    sliderMin, sliderMax, defaultFrom, defaultTo,
    datePreset, onPresetChange: handlePresetChange,
  };

  return { controls, applyFilters, effectiveFromMs, effectiveToMs };
}
