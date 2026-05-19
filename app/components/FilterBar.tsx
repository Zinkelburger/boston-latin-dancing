'use client';

import { useState, useRef, useEffect } from 'react';
import clsx from 'clsx';
import type { DanceStyle, DayOfWeek } from '@/types/event';
import { STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';
import DateRangeSlider, { type DateRangeValue } from './DateRangeSlider';

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
  totalCount: number;
  visibleCount: number;
};

export default function FilterBar({
  selectedStyles, onStylesChange,
  selectedDays, onDaysChange,
  dateMode, onDateModeChange,
  dateSlider, onDateSliderChange,
  sliderMin, sliderMax,
  defaultFrom, defaultTo,
  totalCount, visibleCount,
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

      {/* Row 3: Date range + count */}
      <div className="filter-bar-row filter-bar-date-row">
        <div className="filter-date-section">
          <span className="filter-label">When</span>
          <button
            onClick={setAnyMode}
            className={clsx(
              'pretty-pill text-xs',
              isAny ? 'pretty-pill-rose' : 'pretty-pill-ghost',
            )}
          >
            Any
          </button>
          <button
            onClick={() => {
              onDateModeChange('custom');
              setDateDialogOpen(true);
            }}
            className={clsx(
              'pretty-pill text-xs',
              !isAny ? 'pretty-pill-rose' : 'pretty-pill-ghost',
            )}
          >
            {dateLabel}
          </button>
        </div>
        <span className="filter-count">
          {visibleCount} of {totalCount}
        </span>
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
