'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';
import clsx from 'clsx';
import { formatShort } from '@/lib/dates';
import { StyleFilter, DayFilter, PresetChips, BigEventsToggle } from './FilterControls';
import type { FilterControlsProps } from './useEventFilters';

type Props = FilterControlsProps & {
  open: boolean;
  onClose: () => void;
  /** Opens the custom date-range dialog (owned by the parent). */
  onOpenDateRange: () => void;
  /** Shown on the Done button, e.g. "Show 12 events". Falls back to "Done". */
  resultLabel?: string;
};

/**
 * Phone-sized filter panel. Slides up from the bottom edge so the controls
 * sit in the thumb zone, and lays every group out as justified rows so a
 * stray "Sun" or "Other" never dangles alone on a second line.
 *
 * Shares all of its state with the desktop FilterBar: nothing here is local
 * except the open/closed flag the parent owns.
 */
export default function FilterSheet({
  open, onClose, onOpenDateRange, resultLabel,
  selectedStyles, onStylesChange,
  selectedDays, onDaysChange,
  specialOnly, onSpecialOnlyChange,
  dateMode, onDateModeChange,
  dateSlider,
  datePreset, onPresetChange,
}: Props) {
  const sheetRef = useRef<HTMLDivElement>(null);

  // Escape closes; scroll-lock the page while open so the map doesn't pan
  // under the finger when the sheet's own scroll runs out.
  useEffect(() => {
    if (!open) return;
    const keyHandler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', keyHandler);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', keyHandler);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  const isAny = dateMode === 'any';
  const activeCount = selectedStyles.length + selectedDays.length +
    (dateMode === 'custom' ? 1 : 0) + (specialOnly ? 1 : 0);
  const dateLabel = `${formatShort(dateSlider.fromDay)} – ${formatShort(dateSlider.toDay)}`;

  const clearAll = () => {
    onStylesChange([]);
    onDaysChange([]);
    onSpecialOnlyChange(false);
    onPresetChange(null);
    onDateModeChange('any');
  };

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div
        ref={sheetRef}
        role="dialog"
        aria-modal="true"
        aria-label="Filters"
        className="sheet"
        onClick={e => e.stopPropagation()}
      >
        <div className="sheet-grabber" aria-hidden="true" />

        <div className="sheet-header">
          <h3 className="sheet-title">Filters</h3>
          <button
            type="button"
            onClick={clearAll}
            disabled={activeCount === 0}
            className="sheet-clear"
          >
            Clear all
          </button>
        </div>

        <div className="sheet-body">
          <section className="sheet-group">
            <h4 className="sheet-group-label">Style</h4>
            <StyleFilter selected={selectedStyles} onChange={onStylesChange} />
          </section>

          <section className="sheet-group">
            <h4 className="sheet-group-label">Day</h4>
            <DayFilter selected={selectedDays} onChange={onDaysChange} />
          </section>

          <section className="sheet-group">
            <h4 className="sheet-group-label">When</h4>
            <div className="filter-pills">
              <button
                onClick={() => { onPresetChange(null); onDateModeChange('any'); }}
                className={clsx('pretty-pill text-xs', isAny && !datePreset ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
              >
                Any
              </button>
              <PresetChips datePreset={datePreset} onPresetChange={onPresetChange} />
              <button
                onClick={() => { onDateModeChange('custom'); onOpenDateRange(); }}
                className={clsx('pretty-pill text-xs', !isAny && !datePreset ? 'pretty-pill-rose' : 'pretty-pill-ghost')}
              >
                {!isAny && !datePreset ? dateLabel : 'Pick dates…'}
              </button>
            </div>
          </section>

          <section className="sheet-group">
            <h4 className="sheet-group-label">Type</h4>
            <div className="filter-pills">
              <BigEventsToggle active={specialOnly} onChange={onSpecialOnlyChange} />
            </div>
          </section>

          <div className="sheet-links">
            <Link href="/submit" className="sheet-link">+ Submit an event</Link>
            <a
              href="https://github.com/Zinkelburger/boston-latin-dancing"
              target="_blank"
              rel="noopener"
              className="sheet-link"
            >
              Source on GitHub
            </a>
          </div>
        </div>

        <div className="sheet-footer">
          <button type="button" onClick={onClose} className="pretty-pill pretty-pill-solid-rose sheet-done">
            {resultLabel ?? 'Done'}
          </button>
        </div>
      </div>
    </div>
  );
}
