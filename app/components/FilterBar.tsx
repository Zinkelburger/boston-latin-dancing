'use client';

import { useState } from 'react';
import Link from 'next/link';
import { formatShort } from '@/lib/dates';
import { StyleFilter, DayFilter, DateRangeDialog, WhenPills } from './FilterControls';
import type { FilterControlsProps } from './useEventFilters';

type Props = FilterControlsProps & {
  viewMode: 'map' | 'feed';
  onViewModeToggle: () => void;
};

/**
 * The one filter surface, identical on phones and desktop: three labelled rows.
 * The only difference is that on a phone each row's pills sit on a horizontal
 * rail you swipe, with a fade at the edge to say there is more; wider screens
 * wrap them. Nothing is hidden behind a control on either.
 */
export default function FilterBar({
  selectedStyles, onStylesChange,
  selectedDays, onDaysChange,
  specialOnly, onSpecialOnlyChange,
  dateMode, onDateModeChange,
  dateSlider, onDateSliderChange,
  sliderMin, sliderMax,
  defaultFrom, defaultTo,
  viewMode, onViewModeToggle,
  datePreset, onPresetChange,
}: Props) {
  const [dateDialogOpen, setDateDialogOpen] = useState(false);

  const isAny = dateMode === 'any';
  const dateLabel = `${formatShort(dateSlider.fromDay)} – ${formatShort(dateSlider.toDay)}`;

  return (
    <div className="filter-bar">
      {/* Style row also carries the actions. On a phone they share its line;
          on desktop `display: contents` drops them into the grid's last row. */}
      <div className="filter-bar-row filter-bar-style-row">
        <div className="filter-group">
          <span className="filter-label">Style</span>
          <StyleFilter selected={selectedStyles} onChange={onStylesChange} />
        </div>
        <div className="filter-right-actions">
          {viewMode === 'map' && (
            <>
              <a
                href="https://github.com/Zinkelburger/boston-latin-dancing"
                target="_blank"
                rel="noopener"
                className="pretty-pill pretty-pill-ghost text-xs filter-desktop-only"
                aria-label="GitHub repository"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
                </svg>
              </a>
              <Link
                href="/submit"
                className="pretty-pill pretty-pill-ghost text-xs filter-desktop-only"
              >
                + Submit event
              </Link>
            </>
          )}
          <button
            type="button"
            onClick={onViewModeToggle}
            className="pretty-pill pretty-pill-neutral text-xs"
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

      <div className="filter-bar-row filter-bar-day-row">
        <div className="filter-group">
          <span className="filter-label">Day</span>
          <DayFilter selected={selectedDays} onChange={onDaysChange} />
        </div>
      </div>

      <div className="filter-bar-row filter-bar-date-row">
        <div className="filter-group">
          <span className="filter-label">When</span>
          <WhenPills
            isAny={isAny}
            datePreset={datePreset}
            onPresetChange={onPresetChange}
            onDateModeChange={onDateModeChange}
            onOpenCustom={() => setDateDialogOpen(true)}
            customLabel={dateLabel}
            specialOnly={specialOnly}
            onSpecialOnlyChange={onSpecialOnlyChange}
          />
        </div>
      </div>

      <DateRangeDialog
        open={dateDialogOpen}
        onClose={() => setDateDialogOpen(false)}
        dateMode={dateMode}
        selectedDays={selectedDays}
        onDaysChange={onDaysChange}
        dateSlider={dateSlider}
        onDateSliderChange={onDateSliderChange}
        onDateModeChange={onDateModeChange}
        sliderMin={sliderMin}
        sliderMax={sliderMax}
        defaultFrom={defaultFrom}
        defaultTo={defaultTo}
      />
    </div>
  );
}
