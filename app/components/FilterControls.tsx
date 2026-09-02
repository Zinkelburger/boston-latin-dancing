'use client';

import { useRef, useEffect, useId } from 'react';
import clsx from 'clsx';
import type { DanceStyle, DayOfWeek } from '@/types/event';
import { STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';
import { FILTER_STYLES, DAYS, DAY_SHORT } from '@/lib/filter-options';
import { dayToIso, isoToDay } from '@/lib/dates';
import { computePresetRange, DATE_PRESETS, PRESET_LABELS, type DatePreset } from '@/lib/date-presets';
import DateRangeSlider, { type DateRangeValue } from './DateRangeSlider';

function toggle<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter(x => x !== item) : [...list, item];
}

/** The two presets worth a permanent chip. Tomorrow / Next 7 Days live behind
 *  Custom, as quick picks in the date dialog. */
export const WHEN_PRESETS: DatePreset[] = ['today', 'weekend'];

function rangesMatch(a: DateRangeValue, b: DateRangeValue): boolean {
  return a.fromDay === b.fromDay && a.toDay === b.toDay;
}

/** "Any" + dance-style pills. Merengue is filter-only omitted; see FILTER_STYLES. */
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
        type="button"
        onClick={() => onChange([])}
        aria-pressed={selected.length === 0}
        className={clsx('pretty-pill text-xs', selected.length === 0 ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
      >
        Any
      </button>
      {FILTER_STYLES.map(style => (
        <button
          key={style}
          type="button"
          onClick={() => onChange(toggle(selected, style))}
          aria-pressed={selected.includes(style)}
          className={clsx(
            'pretty-pill text-xs',
            selected.includes(style) ? STYLE_PILL_CLASS[style] : 'pretty-pill-ghost',
          )}
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
  className,
}: {
  active: boolean;
  onChange: (v: boolean) => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!active)}
      aria-pressed={active}
      className={clsx('pretty-pill text-xs', className, active ? 'pretty-pill-fuchsia' : 'pretty-pill-ghost')}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
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
        type="button"
        onClick={() => onChange([])}
        aria-pressed={selected.length === 0}
        className={clsx('pretty-pill text-xs', selected.length === 0 ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
      >
        Any
      </button>
      {DAYS.map(day => (
        <button
          key={day}
          type="button"
          onClick={() => onChange(toggle(selected, day))}
          aria-pressed={selected.includes(day)}
          aria-label={day}
          className={clsx(
            'pretty-pill text-xs',
            selected.includes(day) ? 'pretty-pill-rose' : 'pretty-pill-ghost',
          )}
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
  presets = DATE_PRESETS,
}: {
  datePreset: DatePreset | null;
  onPresetChange: (preset: DatePreset | null) => void;
  presets?: DatePreset[];
}) {
  return (
    <>
      {presets.map(preset => (
        <button
          key={preset}
          type="button"
          onClick={() => onPresetChange(datePreset === preset ? null : preset)}
          aria-pressed={datePreset === preset}
          className={clsx('pretty-pill text-xs', datePreset === preset ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
        >
          {PRESET_LABELS[preset]}
        </button>
      ))}
    </>
  );
}

/** The When row: Any · Today · This Weekend · Custom · Big Events. */
export function WhenPills({
  isAny,
  datePreset,
  onPresetChange,
  onDateModeChange,
  onOpenCustom,
  customLabel,
  specialOnly,
  onSpecialOnlyChange,
}: {
  isAny: boolean;
  datePreset: DatePreset | null;
  onPresetChange: (preset: DatePreset | null) => void;
  onDateModeChange: (mode: 'any' | 'custom') => void;
  onOpenCustom: () => void;
  /** Shown on the Custom pill while a custom range is active. */
  customLabel: string;
  specialOnly: boolean;
  onSpecialOnlyChange: (v: boolean) => void;
}) {
  const customActive = !isAny && !datePreset;
  return (
    <div className="filter-pills">
      <button
        type="button"
        onClick={() => {
          onPresetChange(null);
          onDateModeChange('any');
        }}
        aria-pressed={isAny && !datePreset}
        className={clsx(
          'pretty-pill text-xs',
          isAny && !datePreset ? 'pretty-pill-rose' : 'pretty-pill-ghost',
        )}
      >
        Any
      </button>
      <PresetChips datePreset={datePreset} onPresetChange={onPresetChange} presets={WHEN_PRESETS} />
      <button
        type="button"
        onClick={() => {
          onDateModeChange('custom');
          onOpenCustom();
        }}
        aria-pressed={customActive}
        aria-haspopup="dialog"
        className={clsx('pretty-pill text-xs', customActive ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
      >
        {customActive ? customLabel : 'Custom'}
      </button>
      <BigEventsToggle active={specialOnly} onChange={onSpecialOnlyChange} />
    </div>
  );
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

/** Modal date-range picker: slider + From/To inputs + Reset/Done.
 *  Closes on outside-click and Escape; keeps keyboard focus inside while open
 *  and hands it back to whatever opened it. */
export function DateRangeDialog({
  open,
  onClose,
  dateMode,
  dateSlider,
  onDateSliderChange,
  onDateModeChange,
  sliderMin,
  sliderMax,
  defaultFrom,
  defaultTo,
  selectedDays,
  onDaysChange,
}: {
  open: boolean;
  onClose: () => void;
  dateMode: 'any' | 'custom';
  dateSlider: DateRangeValue;
  onDateSliderChange: (v: DateRangeValue) => void;
  onDateModeChange: (mode: 'any' | 'custom') => void;
  sliderMin: number;
  sliderMax: number;
  defaultFrom: number;
  defaultTo: number;
  /** Same state the Day row drives, so the two surfaces always agree. */
  selectedDays: DayOfWeek[];
  onDaysChange: (days: DayOfWeek[]) => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (dialogRef.current && !dialogRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const keyHandler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      // Minimal focus trap: Tab cycles within the dialog.
      if (e.key !== 'Tab' || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !dialogRef.current.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('mousedown', handler);
    document.addEventListener('keydown', keyHandler);
    return () => {
      document.removeEventListener('mousedown', handler);
      document.removeEventListener('keydown', keyHandler);
    };
  }, [open, onClose]);

  // Focus moves into the dialog on open and back to the opener on close.
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? dialogRef.current)?.focus();
    return () => {
      if (opener?.isConnected) opener.focus();
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="filter-dialog-backdrop">
      <div
        ref={dialogRef}
        className="filter-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="filter-dialog-header">
          <h3 id={titleId}>When</h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="pretty-pill pretty-pill-ghost"
            style={{ padding: '0.15rem 0.45rem', lineHeight: 1 }}
          >
            &#x2715;
          </button>
        </div>

        <div className="filter-dialog-body">
          <div className="filter-dialog-presets-wrap">
            <p className="filter-dialog-presets-label">Quick picks</p>
            <div className="filter-dialog-presets">
              {DATE_PRESETS.map(preset => {
                const range = computePresetRange(preset);
                const active = dateMode === 'custom' && range != null && rangesMatch(dateSlider, range);
                return (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => {
                      if (!range) return;
                      onDateModeChange('custom');
                      onDateSliderChange(range);
                    }}
                    aria-pressed={active}
                    className={clsx('pretty-pill text-xs', active ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
                  >
                    {PRESET_LABELS[preset]}
                  </button>
                );
              })}
            </div>
          </div>

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
              <label htmlFor={`${titleId}-from`}>From</label>
              <input
                id={`${titleId}-from`}
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
              <label htmlFor={`${titleId}-to`}>To</label>
              <input
                id={`${titleId}-to`}
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

          <div className="filter-dialog-presets-wrap">
            <p className="filter-dialog-presets-label">Days of the week</p>
            <p className="filter-dialog-hint">
              Only show certain nights inside the range above.
            </p>
            <DayFilter selected={selectedDays} onChange={onDaysChange} />
          </div>

          <div className="filter-dialog-actions">
            <button
              type="button"
              onClick={() => onDateSliderChange({ fromDay: defaultFrom, toDay: defaultTo })}
              className="pretty-pill pretty-pill-ghost text-sm"
            >
              Reset to default
            </button>
            <button
              type="button"
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
