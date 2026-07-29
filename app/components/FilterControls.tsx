'use client';

import { useRef, useEffect } from 'react';
import clsx from 'clsx';
import type { DanceStyle, DayOfWeek } from '@/types/event';
import { STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';
import { ALL_STYLES, DAYS, DAY_SHORT } from '@/lib/filter-options';
import { dayToIso, isoToDay } from '@/lib/dates';
import { DATE_PRESETS, PRESET_LABELS, type DatePreset } from '@/lib/date-presets';
import DateRangeSlider, { type DateRangeValue } from './DateRangeSlider';

function toggle<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter(x => x !== item) : [...list, item];
}

/** "Any" + dance-style pills. */
export function StyleFilter({
  selected,
  onChange,
}: {
  selected: DanceStyle[];
  onChange: (styles: DanceStyle[]) => void;
}) {
  return (
    <div className="filter-pills">
      <button
        onClick={() => onChange([])}
        className={clsx('pretty-pill text-xs', selected.length === 0 ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
      >
        Any
      </button>
      {ALL_STYLES.map(style => (
        <button
          key={style}
          onClick={() => onChange(toggle(selected, style))}
          className={clsx('pretty-pill text-xs', selected.includes(style) ? STYLE_PILL_CLASS[style] : 'pretty-pill-ghost')}
        >
          {STYLE_LABELS[style]}
        </button>
      ))}
    </div>
  );
}

/** "Big Events" toggle — festivals and big one-offs only, hides weekly socials. */
export function BigEventsToggle({
  active,
  onChange,
}: {
  active: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      onClick={() => onChange(!active)}
      className={clsx('pretty-pill text-xs', active ? 'pretty-pill-amber' : 'pretty-pill-ghost')}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
      </svg>
      Big Events
    </button>
  );
}

/** "Any" + day-of-week pills. */
export function DayFilter({
  selected,
  onChange,
}: {
  selected: DayOfWeek[];
  onChange: (days: DayOfWeek[]) => void;
}) {
  return (
    <div className="filter-pills">
      <button
        onClick={() => onChange([])}
        className={clsx('pretty-pill text-xs', selected.length === 0 ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
      >
        Any
      </button>
      {DAYS.map(day => (
        <button
          key={day}
          onClick={() => onChange(toggle(selected, day))}
          className={clsx('pretty-pill text-xs', selected.includes(day) ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
        >
          {DAY_SHORT[day]}
        </button>
      ))}
    </div>
  );
}

/** Quick-pick date preset chips (Today / Tomorrow / This Weekend / Next 7 Days). */
export function PresetChips({
  datePreset,
  onPresetChange,
}: {
  datePreset: DatePreset | null;
  onPresetChange: (preset: DatePreset | null) => void;
}) {
  return (
    <>
      {DATE_PRESETS.map(preset => (
        <button
          key={preset}
          onClick={() => onPresetChange(datePreset === preset ? null : preset)}
          className={clsx('pretty-pill text-xs', datePreset === preset ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
        >
          {PRESET_LABELS[preset]}
        </button>
      ))}
    </>
  );
}

/** Modal date-range picker: slider + From/To inputs + Reset/Done.
 *  Closes on outside-click and Escape. */
export function DateRangeDialog({
  open,
  onClose,
  dateSlider,
  onDateSliderChange,
  onDateModeChange,
  sliderMin,
  sliderMax,
  defaultFrom,
  defaultTo,
}: {
  open: boolean;
  onClose: () => void;
  dateSlider: DateRangeValue;
  onDateSliderChange: (v: DateRangeValue) => void;
  onDateModeChange: (mode: 'any' | 'custom') => void;
  sliderMin: number;
  sliderMax: number;
  defaultFrom: number;
  defaultTo: number;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (dialogRef.current && !dialogRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const keyHandler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', handler);
    document.addEventListener('keydown', keyHandler);
    return () => {
      document.removeEventListener('mousedown', handler);
      document.removeEventListener('keydown', keyHandler);
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="filter-dialog-backdrop">
      <div ref={dialogRef} className="filter-dialog">
        <div className="filter-dialog-header">
          <h3>Date Range</h3>
          <button
            onClick={onClose}
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
              onClick={() => onDateSliderChange({ fromDay: defaultFrom, toDay: defaultTo })}
              className="pretty-pill pretty-pill-ghost text-sm"
            >
              Reset to default
            </button>
            <button
              onClick={onClose}
              className="pretty-pill pretty-pill-rose text-sm"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
