'use client';

import { useState, useRef, useEffect } from 'react';
import Link from 'next/link';
import clsx from 'clsx';
import type { DanceStyle, DayOfWeek } from '@/types/event';
import { STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';
import DateRangeSlider, { type DateRangeValue } from './DateRangeSlider';
import type { DatePreset } from './MapView';
import { PRESET_LABELS, DATE_PRESETS } from './MapView';

const ALL_STYLES: DanceStyle[] = ['bachata', 'salsa', 'kizomba', 'zouk', 'merengue', 'other'];
const DAYS: DayOfWeek[] = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const DAY_SHORT: Record<DayOfWeek, string> = {
  Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed',
  Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat', Sunday: 'Sun',
};

function dateToDay(d: Date): number {
  return Math.floor(d.getTime() / 86400000);
}

function dayToIso(day: number): string {
  const d = new Date(day * 86400000);
  return d.toISOString().slice(0, 10);
}

function isoToDay(iso: string): number {
  return dateToDay(new Date(iso + 'T00:00:00Z'));
}

function formatShort(day: number): string {
  const d = new Date(day * 86400000);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

type Props = {
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
  viewMode: 'map' | 'feed';
  onViewModeToggle: () => void;
  datePreset: DatePreset | null;
  onPresetChange: (preset: DatePreset | null) => void;
};

export default function FilterBar({
  selectedStyles, onStylesChange,
  selectedDays, onDaysChange,
  dateMode, onDateModeChange,
  dateSlider, onDateSliderChange,
  sliderMin, sliderMax,
  defaultFrom, defaultTo,
  viewMode, onViewModeToggle,
  datePreset, onPresetChange,
}: Props) {
  const [dateDialogOpen, setDateDialogOpen] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);

  const toggleStyle = (style: DanceStyle) => {
    if (selectedStyles.includes(style)) {
      onStylesChange(selectedStyles.filter(s => s !== style));
    } else {
      onStylesChange([...selectedStyles, style]);
    }
  };

  const toggleDay = (day: DayOfWeek) => {
    if (selectedDays.includes(day)) {
      onDaysChange(selectedDays.filter(d => d !== day));
    } else {
      onDaysChange([...selectedDays, day]);
    }
  };

  const setAnyMode = () => {
    onDateModeChange('any');
  };

  const resetSliderToDefault = () => {
    onDateSliderChange({ fromDay: defaultFrom, toDay: defaultTo });
  };

  const isAny = dateMode === 'any';

  useEffect(() => {
    if (!dateDialogOpen) return;
    const handler = (e: MouseEvent) => {
      if (dialogRef.current && !dialogRef.current.contains(e.target as Node)) {
        setDateDialogOpen(false);
      }
    };
    const keyHandler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDateDialogOpen(false);
    };
    document.addEventListener('mousedown', handler);
    document.addEventListener('keydown', keyHandler);
    return () => {
      document.removeEventListener('mousedown', handler);
      document.removeEventListener('keydown', keyHandler);
    };
  }, [dateDialogOpen]);

  const dateLabel = `${formatShort(dateSlider.fromDay)} – ${formatShort(dateSlider.toDay)}`;

  return (
    <div className="filter-bar">
      {/* Row 1: Style */}
      <div className="filter-bar-row">
        <div className="filter-group">
          <span className="filter-label">Style</span>
          <div className="filter-pills">
            <button
              onClick={() => onStylesChange([])}
              className={clsx(
                'pretty-pill text-xs',
                selectedStyles.length === 0 ? 'pretty-pill-rose' : 'pretty-pill-ghost',
              )}
            >
              Any
            </button>
            {ALL_STYLES.map(style => (
              <button
                key={style}
                onClick={() => toggleStyle(style)}
                className={clsx(
                  'pretty-pill text-xs',
                  selectedStyles.includes(style) ? STYLE_PILL_CLASS[style] : 'pretty-pill-ghost',
                )}
              >
                {STYLE_LABELS[style]}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Row 2: Day */}
      <div className="filter-bar-row">
        <div className="filter-group">
          <span className="filter-label">Day</span>
          <div className="filter-pills">
            <button
              onClick={() => onDaysChange([])}
              className={clsx(
                'pretty-pill text-xs',
                selectedDays.length === 0 ? 'pretty-pill-rose' : 'pretty-pill-ghost',
              )}
            >
              Any
            </button>
            {DAYS.map(day => (
              <button
                key={day}
                onClick={() => toggleDay(day)}
                className={clsx(
                  'pretty-pill text-xs',
                  selectedDays.includes(day) ? 'pretty-pill-rose' : 'pretty-pill-ghost',
                )}
              >
                {DAY_SHORT[day]}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Row 3: When presets + date range */}
      <div className="filter-bar-row filter-bar-date-row">
        <div className="filter-date-section">
          <span className="filter-label">When</span>
          <button
            onClick={() => {
              onPresetChange(null);
              setAnyMode();
            }}
            className={clsx(
              'pretty-pill text-xs',
              isAny && !datePreset ? 'pretty-pill-rose' : 'pretty-pill-ghost',
            )}
          >
            Any
          </button>
          {DATE_PRESETS.map(preset => (
            <button
              key={preset}
              onClick={() => onPresetChange(datePreset === preset ? null : preset)}
              className={clsx(
                'pretty-pill text-xs',
                datePreset === preset ? 'pretty-pill-rose' : 'pretty-pill-ghost',
              )}
            >
              {PRESET_LABELS[preset]}
            </button>
          ))}
          <button
            onClick={() => {
              onDateModeChange('custom');
              setDateDialogOpen(true);
            }}
            className={clsx(
              'pretty-pill text-xs',
              !isAny && !datePreset ? 'pretty-pill-rose' : 'pretty-pill-ghost',
            )}
          >
            {dateLabel}
          </button>
        </div>
        <div className="filter-right-actions">
          <a
            href="https://github.com/Zinkelburger/boston-latin-dancing"
            target="_blank"
            rel="noopener"
            className="pretty-pill pretty-pill-ghost text-xs"
            aria-label="GitHub repository"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
            </svg>
          </a>
          <Link
            href="/submit"
            className="pretty-pill pretty-pill-ghost text-xs"
          >
            + Submit event
          </Link>
          <button
            onClick={onViewModeToggle}
            className="pretty-pill pretty-pill-ghost text-xs"
          >
            {viewMode === 'map' ? (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="8" y1="6" x2="21" y2="6" />
                  <line x1="8" y1="12" x2="21" y2="12" />
                  <line x1="8" y1="18" x2="21" y2="18" />
                  <line x1="3" y1="6" x2="3.01" y2="6" />
                  <line x1="3" y1="12" x2="3.01" y2="12" />
                  <line x1="3" y1="18" x2="3.01" y2="18" />
                </svg>
                Feed
              </>
            ) : (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6" />
                  <line x1="8" y1="2" x2="8" y2="18" />
                  <line x1="16" y1="6" x2="16" y2="22" />
                </svg>
                Map
              </>
            )}
          </button>
        </div>
      </div>

      {/* Floating date dialog */}
      {dateDialogOpen && (
        <div className="filter-dialog-backdrop">
          <div ref={dialogRef} className="filter-dialog">
            <div className="filter-dialog-header">
              <h3>Date Range</h3>
              <button
                onClick={() => setDateDialogOpen(false)}
                className="pretty-pill pretty-pill-ghost"
                style={{ padding: '0.15rem 0.45rem', lineHeight: 1 }}
              >
                &#x2715;
              </button>
            </div>

            <div className="filter-dialog-body">
              <DateRangeSlider
                minDay={sliderMin}
                maxDay={sliderMax}
                value={dateSlider}
                onChange={v => {
                  onDateModeChange('custom');
                  onDateSliderChange(v);
                }}
              />

              <div className="filter-dialog-inputs">
                <div className="filter-dialog-field">
                  <label>From</label>
                  <input
                    type="date"
                    value={dayToIso(dateSlider.fromDay)}
                    onChange={e => {
                      if (!e.target.value) return;
                      const day = isoToDay(e.target.value);
                      if (day <= dateSlider.toDay) {
                        onDateModeChange('custom');
                        onDateSliderChange({ ...dateSlider, fromDay: day });
                      }
                    }}
                  />
                </div>
                <div className="filter-dialog-field">
                  <label>To</label>
                  <input
                    type="date"
                    value={dayToIso(dateSlider.toDay)}
                    onChange={e => {
                      if (!e.target.value) return;
                      const day = isoToDay(e.target.value);
                      if (day >= dateSlider.fromDay) {
                        onDateModeChange('custom');
                        onDateSliderChange({ ...dateSlider, toDay: day });
                      }
                    }}
                  />
                </div>
              </div>

              <div className="filter-dialog-actions">
                <button
                  onClick={resetSliderToDefault}
                  className="pretty-pill pretty-pill-ghost text-sm"
                >
                  Reset to default
                </button>
                <button
                  onClick={() => setDateDialogOpen(false)}
                  className="pretty-pill pretty-pill-rose text-sm"
                >
                  Done
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
