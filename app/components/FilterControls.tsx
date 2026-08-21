'use client';

import { useRef, useEffect, useState } from 'react';
import clsx from 'clsx';
import type { DanceStyle, DayOfWeek } from '@/types/event';
import { STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';
import { FILTER_STYLES, PRIMARY_STYLES, DAYS, DAY_SHORT, PRIMARY_DAYS } from '@/lib/filter-options';
import { dayToIso, isoToDay } from '@/lib/dates';
import { computePresetRange, DATE_PRESETS, PRESET_LABELS, type DatePreset } from '@/lib/date-presets';
import DateRangeSlider, { type DateRangeValue } from './DateRangeSlider';

function toggle<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter(x => x !== item) : [...list, item];
}

/** True only where the rows actually collapse. Starts false so the server and
 *  the first paint agree on the wider layout; which pills are visible is decided
 *  in CSS either way, so nothing moves when this flips after hydration. */
function useCollapsibleRows(): boolean {
  const [collapsible, setCollapsible] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 640px)');
    const sync = () => setCollapsible(mq.matches);
    sync();
    mq.addEventListener('change', sync);
    return () => mq.removeEventListener('change', sync);
  }, []);
  return collapsible;
}

/** Row label. On a phone it is the tap target that reveals the extra pills.
 *  Where every pill is already out there is nothing to reveal, so it renders as
 *  plain text rather than a focusable button that does nothing and misreports
 *  the row as collapsed. */
export function FilterLabel({
  children,
  open,
  onToggle,
}: {
  children: string;
  open: boolean;
  onToggle: () => void;
}) {
  const collapsible = useCollapsibleRows();

  if (!collapsible) {
    return <span className="filter-label">{children}</span>;
  }

  return (
    <button
      type="button"
      className={clsx('filter-label', open && 'is-open')}
      onClick={onToggle}
      aria-expanded={open}
    >
      {children}
      <svg
        className="filter-label-chevron"
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <polyline points="9 6 15 12 9 18" />
      </svg>
    </button>
  );
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
        onClick={() => onChange([])}
        className={clsx('pretty-pill text-xs', selected.length === 0 ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
      >
        Any
      </button>
      {FILTER_STYLES.map(style => {
        const extra = !PRIMARY_STYLES.includes(style) && !selected.includes(style);
        return (
          <button
            key={style}
            onClick={() => onChange(toggle(selected, style))}
            className={clsx(
              'pretty-pill text-xs',
              extra && 'filter-pill-extra',
              selected.includes(style) ? STYLE_PILL_CLASS[style] : 'pretty-pill-ghost',
            )}
          >
            {STYLE_LABELS[style]}
          </button>
        );
      })}
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
      onClick={() => onChange(!active)}
      className={clsx('pretty-pill text-xs', className, active ? 'pretty-pill-fuchsia' : 'pretty-pill-ghost')}
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
  showAll = false,
}: {
  selected: DayOfWeek[];
  onChange: (days: DayOfWeek[]) => void;
  /** Skip the phone row's collapsing — the dialog has room for all seven. */
  showAll?: boolean;
}) {
  return (
    <div className="filter-pills">
      <button
        onClick={() => onChange([])}
        className={clsx('pretty-pill text-xs', selected.length === 0 ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
      >
        Any
      </button>
      {DAYS.map(day => {
        const extra = !showAll && !PRIMARY_DAYS.includes(day) && !selected.includes(day);
        return (
          <button
            key={day}
            onClick={() => onChange(toggle(selected, day))}
            className={clsx(
              'pretty-pill text-xs',
              extra && 'filter-pill-extra',
              selected.includes(day) ? 'pretty-pill-rose' : 'pretty-pill-ghost',
            )}
          >
            {DAY_SHORT[day]}
          </button>
        );
      })}
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
          onClick={() => onPresetChange(datePreset === preset ? null : preset)}
          className={clsx('pretty-pill text-xs', datePreset === preset ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
        >
          {PRESET_LABELS[preset]}
        </button>
      ))}
    </>
  );
}

/** The When row: Any · Today · This Weekend · Big Events · Custom. Rendered as
 *  a fragment so the bar and the sheet keep their own wrappers. */
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
    <>
      <button
        onClick={() => {
          onPresetChange(null);
          onDateModeChange('any');
        }}
        className={clsx(
          'pretty-pill text-xs',
          isAny && !datePreset ? 'pretty-pill-rose' : 'pretty-pill-ghost',
        )}
      >
        Any
      </button>
      <PresetChips datePreset={datePreset} onPresetChange={onPresetChange} presets={WHEN_PRESETS} />
      <button
        onClick={() => {
          onDateModeChange('custom');
          onOpenCustom();
        }}
        className={clsx(
          'pretty-pill text-xs',
          !customActive && 'filter-pill-extra',
          customActive ? 'pretty-pill-rose' : 'pretty-pill-ghost',
        )}
      >
        {customActive ? customLabel : 'Custom'}
      </button>
      <BigEventsToggle
        active={specialOnly}
        onChange={onSpecialOnlyChange}
        className={!specialOnly ? 'filter-pill-extra' : undefined}
      />
    </>
  );
}

/** Modal date-range picker: slider + From/To inputs + Reset/Done.
 *  Closes on outside-click and Escape. */
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
          <h3>When</h3>
          <button
            onClick={onClose}
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

          <div className="filter-dialog-presets-wrap">
            <p className="filter-dialog-presets-label">Days of the week</p>
            <p className="filter-dialog-hint">
              Only show certain nights inside the range above.
            </p>
            <DayFilter selected={selectedDays} onChange={onDaysChange} showAll />
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
